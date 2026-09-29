"""Contextual, presentation-safe notices for customer engine surfaces."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Literal

from django.db import DatabaseError
from django.utils import timezone

from qbet.web.models import BonusOffer
from qbet.web.monitoring import EngineNotice, MonitoringEngineStatus
from qbet.web.provider_activity import ProviderActivitySnapshot
from qbet.web.simulation_control import SimulationRunSnapshot


@dataclass(frozen=True)
class BonusInputSnapshot:
    active_offers: int = 0
    ready_offers: int = 0
    provider_count: int = 0
    available: bool = True


def bonus_input_snapshot(*, user_id: int) -> BonusInputSnapshot:
    """Return current user-owned Bonus Offer coverage without inventing thresholds."""

    try:
        offers = tuple(
            BonusOffer.objects.filter(
                user_id=user_id,
                valid_until__gt=timezone.now(),
            )
            .select_related("provider")
            .order_by("valid_until", "id")
        )
        ready = tuple(offer for offer in offers if offer.is_preparation_ready)
    except DatabaseError:
        return BonusInputSnapshot(available=False)

    return BonusInputSnapshot(
        active_offers=len(offers),
        ready_offers=len(ready),
        provider_count=len({offer.provider.provider_id for offer in offers}),
    )


def contextual_engine_statuses(
    engines: Iterable[MonitoringEngineStatus],
    *,
    bonus_input: BonusInputSnapshot | None = None,
    provider_activity: ProviderActivitySnapshot | None = None,
    runs: Iterable[SimulationRunSnapshot] = (),
    bonus_offers_url: str = "/bonus-offers/",
) -> tuple[MonitoringEngineStatus, ...]:
    """Attach current notices while keeping technical counters untouched."""

    latest_runs: dict[str, SimulationRunSnapshot] = {}
    for run in runs:
        latest_runs.setdefault(run.engine, run)

    return tuple(
        replace(
            engine,
            notices=_engine_notices(
                engine,
                bonus_input=bonus_input,
                provider_activity=provider_activity,
                latest_run=latest_runs.get(engine.engine_id),
                bonus_offers_url=bonus_offers_url,
            ),
        )
        for engine in engines
    )


def _engine_notices(
    engine: MonitoringEngineStatus,
    *,
    bonus_input: BonusInputSnapshot | None,
    provider_activity: ProviderActivitySnapshot | None,
    latest_run: SimulationRunSnapshot | None,
    bonus_offers_url: str,
) -> tuple[EngineNotice, ...]:
    notices: list[EngineNotice] = []

    if engine.live_state == "error" and engine.detail == "Control unavailable.":
        notices.append(
            EngineNotice(
                severity="error",
                reason_code="engine_control_unavailable",
                title="Engine control unavailable",
                detail="Q-Bet cannot read the current engine control state.",
            )
        )

    if latest_run is not None and latest_run.status == "failed" and latest_run.error_message:
        notices.append(_run_failure_notice(latest_run.error_message, bonus_offers_url))

    if engine.engine_id == "bonus" and bonus_input is not None:
        notice = _bonus_input_notice(bonus_input, bonus_offers_url)
        if notice is not None:
            notices.append(notice)

    if provider_activity is not None and engine.enabled:
        notice = _provider_notice(provider_activity)
        if notice is not None:
            notices.append(notice)

    if engine.latest_no_opportunity:
        notices.append(
            EngineNotice(
                severity="info",
                reason_code="no_valid_opportunity",
                title="No valid opportunity found",
                detail=(
                    "Current market data was evaluated successfully, but no profitable "
                    "opportunity passed the current strategy criteria."
                ),
                occurred_at=engine.latest_report_generated_at,
            )
        )

    seen_titles: set[str] = set()
    unique: list[EngineNotice] = []
    for notice in notices:
        if notice.title in seen_titles:
            continue
        seen_titles.add(notice.title)
        unique.append(notice)
    return tuple(unique)


def _bonus_input_notice(
    snapshot: BonusInputSnapshot,
    bonus_offers_url: str,
) -> EngineNotice | None:
    if not snapshot.available:
        return EngineNotice(
            severity="error",
            reason_code="bonus_input_unavailable",
            title="Bonus input unavailable",
            detail="Q-Bet cannot read your current Bonus Offers.",
        )
    if snapshot.active_offers == 0:
        return EngineNotice(
            severity="warning",
            reason_code="bonus_offer_missing",
            title="Bonus input needed",
            detail="No active Bonus Offers are available for BonusEngine.",
            action_label="Manage Bonus Offers",
            action_url=bonus_offers_url,
        )
    if snapshot.ready_offers == 0:
        return EngineNotice(
            severity="warning",
            reason_code="bonus_offer_unavailable",
            title="Bonus offers need attention",
            detail=(
                f"{snapshot.active_offers} active offer(s) are recorded across "
                f"{snapshot.provider_count} provider(s), but none is ready for API-backed preparation."
            ),
            action_label="Review Bonus Offers",
            action_url=bonus_offers_url,
        )
    if snapshot.ready_offers < snapshot.active_offers:
        return EngineNotice(
            severity="warning",
            reason_code="bonus_offer_partially_ready",
            title="Some Bonus Offers need attention",
            detail=(
                f"{snapshot.ready_offers} of {snapshot.active_offers} active offer(s) across "
                f"{snapshot.provider_count} provider(s) are ready for API-backed preparation."
            ),
            action_label="Review Bonus Offers",
            action_url=bonus_offers_url,
        )
    return None


def provider_activity_notice(activity: ProviderActivitySnapshot) -> EngineNotice | None:
    """Project the latest global provider observation without engine attribution."""

    return _provider_notice(activity)


def _provider_notice(activity: ProviderActivitySnapshot) -> EngineNotice | None:
    provider = f" from {activity.provider}" if activity.provider else ""
    if activity.state == "working":
        return EngineNotice(
            severity="info",
            reason_code=activity.reason_code or "provider_refresh_working",
            title="Market data is being refreshed",
            detail=f"A market-data request{provider} is currently running.",
            dismissible=False,
            occurred_at=activity.occurred_at,
        )
    if activity.state == "success":
        return EngineNotice(
            severity="success",
            reason_code=activity.reason_code or "provider_refresh_success",
            title="Market data updated",
            detail=f"The latest market-data request{provider} completed successfully.",
            occurred_at=activity.occurred_at,
        )
    if activity.state == "delayed":
        return EngineNotice(
            severity="warning",
            reason_code=activity.reason_code or "provider_refresh_delayed",
            title="Market data refresh is delayed",
            detail=f"The latest market-data request{provider} is delayed or rate-limited.",
            occurred_at=activity.occurred_at,
        )
    if activity.state == "unavailable":
        return EngineNotice(
            severity="error",
            reason_code=activity.reason_code or "provider_unavailable",
            title="Market data source unavailable",
            detail=f"The current market-data source{provider} is unavailable.",
            occurred_at=activity.occurred_at,
        )
    if activity.state == "error":
        return EngineNotice(
            severity="error",
            reason_code=activity.reason_code or "provider_refresh_failed",
            title="Market data update failed",
            detail=f"The latest market-data request{provider} failed.",
            occurred_at=activity.occurred_at,
        )
    return None


def _run_failure_notice(reason_code: str, bonus_offers_url: str) -> EngineNotice:
    severity: Literal["warning", "error"]
    action_label: str | None = None
    action_url: str | None = None

    if reason_code in {"bonus_offer_missing", "bonus_offer_user_missing"}:
        severity, title, detail = (
            "warning",
            "Bonus input needed",
            "An active Bonus Offer is required before BonusEngine can run.",
        )
        action_label, action_url = "Manage Bonus Offers", bonus_offers_url
    elif reason_code == "bonus_offer_unavailable":
        severity, title, detail = (
            "warning",
            "Bonus offers need attention",
            "Active Bonus Offers exist, but none is currently ready for API-backed preparation.",
        )
        action_label, action_url = "Review Bonus Offers", bonus_offers_url
    elif reason_code in {"bonus_provider_state_unavailable", "bonus_provider_state_mismatch"}:
        severity, title, detail = (
            "error",
            "Provider account state unavailable",
            "The provider account/risk state required by BonusEngine could not be verified.",
        )
    elif reason_code == "bonus_financial_terms_missing":
        severity, title, detail = (
            "warning",
            "Provider financial terms are missing",
            "Explicit provider fee and tax terms are required before this BonusEngine run can continue.",
        )
    elif reason_code == "bonus_financial_terms_invalid":
        severity, title, detail = (
            "error",
            "Provider financial terms are invalid",
            "The configured provider fee or tax terms could not be validated.",
        )
    elif "rate_limit" in reason_code or "rate_limited" in reason_code:
        severity, title, detail = (
            "warning",
            "Market data refresh is delayed",
            "The latest provider request was rate-limited. Q-Bet stopped safely.",
        )
    elif "provider_unavailable" in reason_code:
        severity, title, detail = (
            "error",
            "Market data source unavailable",
            "The market-data provider was unavailable. Q-Bet stopped safely.",
        )
    elif "configuration" in reason_code:
        severity, title, detail = (
            "error",
            "Market data configuration unavailable",
            "The configured market-data source could not be used.",
        )
    elif "auth" in reason_code:
        severity, title, detail = (
            "error",
            "Market data authentication failed",
            "The market-data provider rejected the configured authentication.",
        )
    elif "invalid_payload" in reason_code:
        severity, title, detail = (
            "error",
            "Market data response could not be validated",
            "The provider response did not match the required safe data contract.",
        )
    elif reason_code == "simulation_market_preparation_failed":
        severity, title, detail = (
            "warning",
            "Market data could not be prepared",
            "The current market data could not be converted into a valid simulation input.",
        )
    else:
        severity, title, detail = (
            "error",
            "Simulation needs attention",
            "The latest simulation failed safely. Technical details remain available in Monitoring.",
        )

    return EngineNotice(
        severity=severity,
        reason_code=reason_code,
        title=title,
        detail=detail,
        action_label=action_label,
        action_url=action_url,
    )
