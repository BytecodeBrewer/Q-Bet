"""Protected hosted wake-up boundary for bounded Smart Polling work."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import product
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from qbet.data import (
    DataSourceMetadata,
    SourceTransport,
    THE_ODDS_API_PROVIDER_ID,
    TheOddsApiAdapter,
)
from qbet.data.polling import (
    PollingStrategyResolutionError,
    PollingTarget,
    SmartPollingPolicy,
)
from qbet.data.polling_runtime import PollingWork, SmartPollingRuntime
from qbet.storage.ledger import (
    RoutingConfigurationPersistenceError,
    RoutingConfigurationRepository,
    UserRoutingPreferencePersistenceError,
    UserRoutingPreferenceRepository,
)
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.storage.polling import PollingStrategyPersistenceError, PollingStrategyRepository
from qbet.storage.polling_work import PollingWorkPersistenceError, PostgresPollingWorkRepository
from qbet.workflow.routing import effective_engine_modes


class PollingTickConfigurationError(RuntimeError):
    """Safe configuration failure for the hosted polling boundary."""


def _event_start(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise PollingTickConfigurationError("polling_event_start_invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PollingTickConfigurationError("polling_event_start_invalid")
    return parsed.astimezone(UTC)


def _assumed_liquidity(value: object) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise PollingTickConfigurationError("polling_liquidity_invalid") from error
    if not amount.is_finite() or amount <= 0:
        raise PollingTickConfigurationError("polling_liquidity_invalid")
    return amount


def configured_polling_work(*, now: datetime) -> tuple[PollingWork, ...]:
    """Project persisted route intent into the currently connected polling target."""

    routing = RoutingConfigurationRepository().load()
    if routing is None:
        return ()

    preferences = UserRoutingPreferenceRepository().list()
    engines: list[Literal["bonus", "sports_capital"]] = []
    for engine in ("bonus", "sports_capital"):
        if any(
            effective_engine_modes(routing, preference, engine).simulation
            for _, preference in preferences
        ):
            engines.append(engine)
    if not engines:
        return ()

    if getattr(settings, "QBET_SIMULATION_SPORTS_SOURCE", "") != "the_odds_api":
        return ()

    sports = getattr(settings, "QBET_POLLING_DISCOVERY_SPORTS", ())
    if sports:
        return _discovery_work(now=now, sports=sports, engines=tuple(engines))
    if "sports_capital" not in engines:
        return ()

    values = {
        "sport": getattr(settings, "QBET_SIMULATION_ODDS_SPORT", ""),
        "event_id": getattr(settings, "QBET_SIMULATION_ODDS_EVENT_ID", ""),
        "market": getattr(settings, "QBET_SIMULATION_ODDS_MARKET", ""),
        "event_starts_at": getattr(settings, "QBET_SIMULATION_ODDS_EVENT_STARTS_AT", ""),
        "assumed_liquidity": getattr(settings, "QBET_SIMULATION_ASSUMED_LIQUIDITY", ""),
    }
    if any(not str(value).strip() for value in values.values()):
        raise PollingTickConfigurationError("polling_target_configuration_missing")

    source = DataSourceMetadata(
        provider_id=THE_ODDS_API_PROVIDER_ID,
        source_id="simulation-the-odds-api",
        transport=SourceTransport.API,
    )
    event_starts_at = _event_start(values["event_starts_at"])
    correlation_id = uuid5(
        NAMESPACE_URL,
        ":".join(
            (
                "qbet-polling",
                source.provider_id,
                source.source_id,
                PollingTarget.MARKET.value,
                "sports_capital",
                "simulation",
                str(values["sport"]),
                str(values["event_id"]),
                str(values["market"]),
            )
        ),
    )
    candidate = PollingWork(
        source=source,
        target=PollingTarget.MARKET,
        engine="sports_capital",
        mode="simulation",
        match_id=str(values["event_id"]),
        correlation_id=correlation_id,
        sport=str(values["sport"]),
        market=str(values["market"]),
        event_starts_at=event_starts_at,
        next_due_at=now,
    )
    try:
        strategy = PollingStrategyRepository().resolver().resolve(candidate.request())
    except PollingStrategyResolutionError as error:
        raise PollingTickConfigurationError(error.reason_code) from error
    return (candidate.model_copy(update={"disabled": not strategy.enabled}),)


def _discovery_work(
    *,
    now: datetime,
    sports: tuple[str, ...],
    engines: tuple[Literal["bonus", "sports_capital"], ...],
) -> tuple[PollingWork, ...]:
    if len(sports) > 10 or len(set(sports)) != len(sports) or any(not s.strip() for s in sports):
        raise PollingTickConfigurationError("polling_discovery_sports_invalid")
    market = getattr(settings, "QBET_SIMULATION_ODDS_MARKET", "")
    limit = getattr(settings, "QBET_POLLING_DISCOVERY_MAX_EVENTS", 20)
    if market != "h2h" or not 1 <= limit <= 100:
        raise PollingTickConfigurationError("polling_discovery_configuration_invalid")
    source = DataSourceMetadata(
        provider_id=THE_ODDS_API_PROVIDER_ID,
        source_id="simulation-the-odds-api",
        transport=SourceTransport.API,
    )
    stored = PostgresPollingWorkRepository().list()
    resolver = PollingStrategyRepository().resolver()
    configured: list[PollingWork] = []
    for engine, sport in product(engines, sports):
        identity = f"discovery:{sport}:{market}"
        discovery = PollingWork(
            source=source,
            target=PollingTarget.MARKET,
            engine=engine,
            mode="simulation",
            match_id=identity,
            sport=sport,
            market=market,
            correlation_id=uuid5(NAMESPACE_URL, f"qbet-discovery:{engine}:{identity}"),
            event_starts_at=now + timedelta(days=365),
            next_due_at=now,
            discovery=True,
            discovery_limit=limit,
        )
        try:
            strategy = resolver.resolve(discovery.request())
        except PollingStrategyResolutionError as error:
            raise PollingTickConfigurationError(error.reason_code) from error
        discovery = discovery.model_copy(update={"disabled": not strategy.enabled})
        configured.append(discovery)
        previous = next((w for w in stored if w.identity == discovery.identity), None)
        if previous is None:
            continue
        # The parent record is the recoverable discovery handoff. Synchronization
        # materializes its event targets idempotently on this or a later wake.
        for event in previous.discovered_events:
            configured.append(
                discovery.model_copy(
                    update={
                        "discovery": False,
                        "discovered_events": (),
                        "match_id": event.event_id,
                        "event_starts_at": event.starts_at,
                        "correlation_id": uuid5(
                            NAMESPACE_URL,
                            f"qbet-discovered:{engine}:{sport}:{event.event_id}:{market}",
                        ),
                        "terminal": event.starts_at <= now,
                        "last_outcome": "terminal" if event.starts_at <= now else None,
                        "last_reason": "discovery_event_expired"
                        if event.starts_at <= now
                        else None,
                    }
                )
            )
    return tuple(configured)


def _runtime(*, configured: bool) -> SmartPollingRuntime:
    available_stake = (
        _assumed_liquidity(getattr(settings, "QBET_SIMULATION_ASSUMED_LIQUIDITY", ""))
        if configured
        else Decimal(0)
    )
    return SmartPollingRuntime(
        repository=PostgresPollingWorkRepository(),
        policy=SmartPollingPolicy(resolver=PollingStrategyRepository().resolver()),
        collector=TheOddsApiAdapter(available_stake=available_stake),
        monitoring_writer=PostgresMonitoringRepository(),
        defer_interval=timedelta(seconds=settings.QBET_POLLING_DEFER_SECONDS),
    )


@csrf_exempt
@require_POST
def polling_tick(request: HttpRequest) -> JsonResponse:
    """Run one authenticated, bounded polling unit suitable for a hosted scheduler."""

    token = settings.QBET_POLLING_TICK_TOKEN
    supplied = request.headers.get("Authorization", "")
    if not token or not constant_time_compare(supplied, f"Bearer {token}"):
        return JsonResponse({"detail": "Not found."}, status=404)

    now = datetime.now(UTC)
    try:
        configured = configured_polling_work(now=now)
        result = _runtime(configured=bool(configured)).tick(
            active_work=configured,
            now=now,
            limit=settings.QBET_POLLING_TICK_MAX_WORK,
            lease_for=timedelta(seconds=settings.QBET_POLLING_CLAIM_SECONDS),
        )
    except (
        PollingTickConfigurationError,
        PollingStrategyPersistenceError,
        PollingWorkPersistenceError,
        RoutingConfigurationPersistenceError,
        UserRoutingPreferencePersistenceError,
    ):
        return JsonResponse(
            {"status": "unavailable", "reason": "polling_runtime_unavailable"},
            status=503,
        )

    return JsonResponse(
        {
            "status": "ok",
            "processed": result.processed,
            "provider_requests": result.provider_requests,
            "outcomes": [outcome.value for outcome in result.outcomes],
        }
    )
