"""Protected hosted wake-up boundary for one bounded Smart Polling tick."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from qbet.data.polling_runtime import SmartPollingRuntime
from qbet.data.polling_work import PollingMarketSelection
from qbet.data.the_odds_api import THE_ODDS_API_PROVIDER_ID, TheOddsApiAdapter
from qbet.storage.ledger import (
    RoutingConfigurationPersistenceError,
    RoutingConfigurationRepository,
    UserRoutingPreferencePersistenceError,
    UserRoutingPreferenceRepository,
)
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.storage.polling import (
    PollingStrategyPersistenceError,
    PollingStrategyRepository,
    PollingWorkPersistenceError,
    PollingWorkRepository,
)
from qbet.workflow.routing import RoutingConfiguration


class PollingTickConfigurationError(RuntimeError):
    """Safe deployment configuration failure for the internal tick boundary."""


@csrf_exempt
@require_POST
def polling_tick(request: HttpRequest) -> HttpResponse:
    """Run one bounded, token-protected polling unit; never execute live orders."""

    token = settings.QBET_POLLING_TICK_TOKEN
    supplied = request.headers.get("Authorization", "")
    if not token or not constant_time_compare(supplied, f"Bearer {token}"):
        return HttpResponse("Not found.", status=404, content_type="text/plain; charset=utf-8")

    try:
        selection = _configured_selection()
        routing = RoutingConfigurationRepository().load() or RoutingConfiguration()
        preferences = UserRoutingPreferenceRepository().list()
        summary = _runtime().tick(
            selection=selection,
            routing=routing,
            preferences=preferences,
            now=datetime.now(UTC),
            max_work=settings.QBET_POLLING_TICK_MAX_WORK,
        )
    except PollingTickConfigurationError:
        return JsonResponse({"status": "configuration_unavailable"}, status=503)
    except (
        PollingStrategyPersistenceError,
        PollingWorkPersistenceError,
        RoutingConfigurationPersistenceError,
        UserRoutingPreferencePersistenceError,
    ):
        return JsonResponse({"status": "persistence_unavailable"}, status=503)

    return JsonResponse({"status": "ok", **asdict(summary)})


def _runtime() -> SmartPollingRuntime:
    return SmartPollingRuntime(
        work_store=PollingWorkRepository(),
        strategy_store=PollingStrategyRepository(),
        collectors={THE_ODDS_API_PROVIDER_ID: TheOddsApiAdapter()},
        monitoring_writer=PostgresMonitoringRepository(),
    )


def _configured_selection() -> PollingMarketSelection:
    if not all(
        (
            settings.QBET_POLLING_ODDS_SPORT,
            settings.QBET_POLLING_ODDS_EVENT_ID,
            settings.QBET_POLLING_ODDS_MARKET,
            settings.QBET_POLLING_ODDS_EVENT_STARTS_AT,
        )
    ):
        raise PollingTickConfigurationError("polling target configuration is incomplete")
    try:
        event_starts_at = datetime.fromisoformat(
            settings.QBET_POLLING_ODDS_EVENT_STARTS_AT.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise PollingTickConfigurationError("polling event time is invalid") from error
    if event_starts_at.tzinfo is None or event_starts_at.utcoffset() is None:
        raise PollingTickConfigurationError("polling event time must include a timezone")
    return PollingMarketSelection(
        sport=settings.QBET_POLLING_ODDS_SPORT,
        event_id=settings.QBET_POLLING_ODDS_EVENT_ID,
        market=settings.QBET_POLLING_ODDS_MARKET,
        event_starts_at=event_starts_at,
    )
