"""Durable snapshot -> existing shared Admin Simulation workflow handoff.

The row lock and outer transaction include the run, report and ledger writes.
A terminated worker rolls back all effects and leaves the work claimable.
This consumer never submits Execution work or sends external notifications.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from typing import cast

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db import DatabaseError, transaction
from django.utils import timezone
from pydantic import ValidationError

from qbet.data import DataCollectionRequest, NormalizedMarketSnapshot
from qbet.data.polling_runtime import PollingWork
from qbet.simulation.models import SimulationEngine, SimulationRunConfig
from qbet.simulation.opportunity_source import (
    SimulationOpportunitySource,
    SimulationOpportunitySourceError,
    TheOddsApiSportsSimulationConfig,
    TheOddsApiSportsSimulationOpportunitySource,
)
from qbet.storage.models import MarketEvaluationRow, SimulationReportRow
from qbet.storage.polling import PollingStrategyRepository
from qbet.storage.ledger import RoutingConfigurationRepository, UserRoutingPreferenceRepository
from qbet.workflow.routing import effective_engine_modes
from qbet.web.bonus_offer_simulation import (
    BonusOfferSimulationConfig,
    BonusOfferSimulationOpportunitySource,
)
from qbet.web.models import BonusOffer
from qbet.web.simulation_control import (
    SimulationAlreadyRunningError,
    SimulationControlError,
    SimulationControlService,
)


class MarketEvaluationPersistenceError(RuntimeError):
    pass


def _configuration(work: PollingWork) -> dict:
    values = {
        "sport": work.sport,
        "event_id": work.match_id,
        "market": work.market,
        "assumed_liquidity": str(settings.QBET_SIMULATION_ASSUMED_LIQUIDITY),
        "stake_precision": str(settings.QBET_SIMULATION_STAKE_PRECISION),
    }
    if work.engine == "sports_capital":
        values["requested_total_stake"] = str(settings.QBET_SIMULATION_REQUESTED_TOTAL_STAKE)
    else:
        values["financial_terms_version"] = str(settings.QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS)
    return values


def enqueue_snapshot(work: PollingWork) -> None:
    """Called inside the snapshot producer's transaction, never refetching data."""
    if work.discovery or work.latest_snapshot is None or work.mode != "simulation":
        return
    if work.engine not in ("sports_capital", "bonus"):
        return
    payload = {
        "context": "shared-admin-sandbox",
        "work": work.model_dump(mode="json"),
        "configuration": _configuration(work),
        "promotions": list(
            BonusOffer.objects.filter(user__is_staff=True, user__is_active=True, retired_at=None)
            .order_by("pk")
            .values_list("pk", "version", "user_id")
        )
        if work.engine == "bonus"
        else [],
    }
    # Receipt timestamps are part of the exact immutable snapshot version.
    key = {
        "context": payload["context"],
        "identity": work.identity,
        "snapshot": work.latest_snapshot.model_dump(mode="json"),
        "configuration": payload["configuration"],
        "promotions": payload["promotions"],
    }
    version = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    MarketEvaluationRow.objects.get_or_create(
        evaluation_id=uuid5(NAMESPACE_URL, f"qbet-evaluation:{version}"),
        defaults={"payload": payload, "next_due_at": timezone.now()},
    )


class PersistedSnapshotCollector:
    def __init__(self, snapshot: NormalizedMarketSnapshot) -> None:
        self.snapshot = snapshot

    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        if request.event_id != self.snapshot.event_id or request.sport != self.snapshot.sport:
            raise SimulationOpportunitySourceError("snapshot_identity_mismatch", "Market changed.")
        return self.snapshot.model_copy(update={"correlation_id": request.correlation_id})


def _source(work: PollingWork, payload: dict) -> tuple[SimulationOpportunitySource, User | None]:
    assert work.latest_snapshot is not None
    collector = PersistedSnapshotCollector(work.latest_snapshot)
    if work.engine == "sports_capital":
        return TheOddsApiSportsSimulationOpportunitySource(
            TheOddsApiSportsSimulationConfig.model_validate(payload["configuration"]),
            collector=collector,
        ), None
    promotions = payload["promotions"]
    if not promotions:
        raise SimulationOpportunitySourceError("bonus_offer_user_missing", "No admin promotion.")
    routing = RoutingConfigurationRepository().load()
    preferences = dict(UserRoutingPreferenceRepository().list())
    config = BonusOfferSimulationConfig.model_validate(
        {
            key: value
            for key, value in payload["configuration"].items()
            if key != "financial_terms_version"
        }
    )
    last_error = None
    # Preparation is read-only; only a successful eligible owner starts a run.
    for owner_id in dict.fromkeys(item[2] for item in promotions):
        preference = preferences.get(str(owner_id))
        if routing is None or preference is None or not effective_engine_modes(
            routing, preference, "bonus"
        ).simulation:
            continue
        owner = cast(
            User | None,
            get_user_model().objects.filter(pk=owner_id, is_active=True, is_staff=True).first(),
        )
        if owner is None:
            continue
        source = BonusOfferSimulationOpportunitySource(
            user_id=owner.pk, config=config, collector=collector,
            offer_versions={item[0]: item[1] for item in promotions if item[2] == owner_id},
        )
        try:
            source.build(
                SimulationRunConfig(
                    engine=SimulationEngine.BONUS,
                    starting_capital=Decimal("1"),
                    max_duration=timedelta(seconds=30),
                ),
                work.correlation_id,
            )
        except SimulationOpportunitySourceError as error:
            if error.reason_code in _RETRYABLE_REASONS:
                raise SimulationControlError(error.user_message, reason_code=error.reason_code) from error
            last_error = error
            continue
        return source, owner
    if last_error is not None:
        raise last_error
    raise SimulationOpportunitySourceError("bonus_offer_user_missing", "No eligible admin promotion.")


_RETRYABLE_REASONS = frozenset({
    "simulation_failed",
    "bonus_provider_catalog_unavailable",
    "bonus_market_provider_unavailable",
    "bonus_provider_state_unavailable",
})


def consume_snapshots(*, active_work: tuple[PollingWork, ...], now: datetime, limit: int) -> int:
    routing = RoutingConfigurationRepository().load()
    if routing is None:
        return 0
    staff_ids = {
        str(pk)
        for pk in get_user_model()
        .objects.filter(
            is_active=True,
            is_staff=True,
        )
        .values_list("pk", flat=True)
    }
    preferences = UserRoutingPreferenceRepository().list()
    allowed = {
        work.identity
        for work in active_work
        if not work.disabled
        and not work.terminal
        and any(
            user_id in staff_ids
            and effective_engine_modes(
                routing,
                preference,
                work.engine,
            ).simulation
            for user_id, preference in preferences
        )
    }
    completed = 0
    try:
        candidates = list(
            MarketEvaluationRow.objects.filter(
                outcome__in=("unevaluated", "retry"),
                next_due_at__lte=now,
                payload__work__correlation_id__in=[
                    str(work.correlation_id) for work in active_work if work.identity in allowed
                ],
            ).values_list("pk", flat=True)[:limit]
        )
        for identity in candidates:
            try:
                with transaction.atomic():
                    row = (
                        MarketEvaluationRow.objects.select_for_update(skip_locked=True)
                        .filter(
                            pk=identity,
                            outcome__in=("unevaluated", "retry"),
                            next_due_at__lte=now,
                        )
                        .first()
                    )
                    if row is None:
                        continue
                    work = PollingWork.model_validate(row.payload["work"])
                    if work.identity not in allowed:
                        continue
                    row.attempts += 1
                    _evaluate(row, work, now)
                    row.save()
                    completed += 1
            except SimulationAlreadyRunningError:
                MarketEvaluationRow.objects.filter(
                    pk=identity, outcome__in=("unevaluated", "retry")
                ).update(
                    outcome="retry",
                    reason="simulation_not_ready",
                    next_due_at=now + timedelta(seconds=30),
                )
            except SimulationControlError as error:
                MarketEvaluationRow.objects.filter(
                    pk=identity, outcome__in=("unevaluated", "retry")
                ).update(
                    outcome="missing_input"
                    if error.reason_code and error.reason_code not in _RETRYABLE_REASONS
                    else "retry",
                    reason=error.reason_code or "simulation_not_ready",
                    next_due_at=now + timedelta(seconds=30),
                )
        return completed
    except DatabaseError as error:
        raise MarketEvaluationPersistenceError("market evaluation storage unavailable") from error


def _evaluate(row: MarketEvaluationRow, work: PollingWork, now: datetime) -> None:
    if _configuration(work) != row.payload["configuration"]:
        row.outcome, row.reason = "superseded", "configuration_changed"
        return
    if work.engine == "bonus":
        versions = list(
            BonusOffer.objects.filter(
                user__is_staff=True,
                user__is_active=True,
                retired_at=None,
            )
            .order_by("pk")
            .values_list("pk", "version", "user_id")
        )
        if [list(item) for item in versions] != row.payload["promotions"]:
            row.outcome, row.reason = "superseded", "promotion_changed"
            return
    snapshot = work.latest_snapshot
    assert snapshot is not None
    window = PollingStrategyRepository().resolver().resolve(work.request()).freshness_window
    if work.event_starts_at <= now or any(
        offer.source_updated_at is None
        or offer.source_updated_at > now
        or now - offer.source_updated_at >= window
        for offer in snapshot.offers
    ):
        row.outcome, row.reason = "missing_input", "snapshot_not_current"
        return
    try:
        snapshot.require_ready_for_preparation()
        source, owner = _source(work, row.payload)
    except (ValidationError, ValueError, SimulationOpportunitySourceError) as error:
        row.outcome = "missing_input"
        row.reason = getattr(error, "reason_code", "snapshot_not_ready")
        return
    service = SimulationControlService(opportunity_source=source)
    # BEGIN locks the shared portfolio; all nested writes commit with this job.
    run = service.begin(engine=SimulationEngine(work.engine), initiated_by=owner, run_id=row.pk)
    run = service.run(run.run_id)
    report = SimulationReportRow.objects.get(pk=run.report_id)
    from qbet.reporting import SimulationReport

    result = SimulationReport.model_validate_json(report.payload)
    row.run_id = run.run_id
    evaluation = service.last_workflow_result
    if (
        evaluation is not None
        and evaluation.evaluated_candidates
        and not any(candidate.is_profitable for candidate in evaluation.evaluated_candidates)
    ):
        row.outcome = "unprofitable"
    elif result.status.value != "completed":
        row.outcome = "rejected"
    else:
        row.outcome = "simulated"
    row.reason = "workflow_completed"
