"""Read-only The Odds API adapter for normalized market snapshots."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from pydantic import ValidationError

from qbet.data.models import (
    CompletenessStatus,
    DataCollectionRequest,
    DiscoveredEvent,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
)
from qbet.domain.models import Currency, OfferSide
from qbet.providers import load_german_sportsbook_catalog

THE_ODDS_API_PROVIDER_ID = "the_odds_api"
_BASE_URL = "https://api.the-odds-api.com/v4"

HttpGet = Callable[[str], tuple[int, Mapping[str, str], bytes]]
Clock = Callable[[], datetime]


class TheOddsApiError(RuntimeError):
    """A read-only provider failure that does not expose credentials."""


class TheOddsApiConfigurationError(TheOddsApiError):
    """Raised when required application-bound configuration is missing."""


class TheOddsApiAuthenticationError(TheOddsApiError):
    """Raised when The Odds API rejects configured credentials."""


class TheOddsApiRateLimitError(TheOddsApiError):
    """Raised when The Odds API rejects a request because quota is exhausted."""


class TheOddsApiTransportError(TheOddsApiError):
    """Raised when the provider cannot be reached or returns a temporary failure."""


class TheOddsApiPayloadError(TheOddsApiError):
    """Raised when provider data cannot safely become a normalized snapshot."""


class TheOddsApiAdapter:
    """Fetch one selected event market and normalize it at the data boundary."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        region: str = "eu",
        currency: Currency = "EUR",
        available_stake: Decimal = Decimal(0),
        http_get: HttpGet | None = None,
        clock: Clock | None = None,
        freshness_window: timedelta = timedelta(minutes=5),
    ) -> None:
        if not region.strip():
            raise ValueError("region must not be blank")
        if available_stake.is_nan() or available_stake.is_infinite() or available_stake < 0:
            raise ValueError("available_stake must be a finite non-negative decimal")
        self._api_key = api_key
        self._region = region
        self._currency: Currency = currency
        self._available_stake = available_stake
        self._http_get = http_get or _default_http_get
        self._clock = clock or (lambda: datetime.now(UTC))
        if freshness_window <= timedelta():
            raise ValueError("freshness_window must be positive")
        self._freshness_window = freshness_window
        self._quota_exhausted = False

    def _get(self, url: str) -> tuple[int, Mapping[str, str], bytes]:
        if self._quota_exhausted:
            raise TheOddsApiRateLimitError("The Odds API reported exhausted request quota")
        status, headers, response = self._http_get(url)
        remaining = next(
            (v for k, v in headers.items() if k.lower() == "x-requests-remaining"), None
        )
        if remaining is not None:
            try:
                self._quota_exhausted = int(remaining) <= 0
            except ValueError:
                raise TheOddsApiPayloadError("The Odds API quota header is invalid") from None
        if len(response) > 2_000_000:
            raise TheOddsApiPayloadError("The Odds API response exceeds the payload limit")
        return status, headers, response

    def discover(
        self, request: DataCollectionRequest, *, limit: int
    ) -> tuple[DiscoveredEvent, ...]:
        """One bounded event-list request, without fetching unconfigured sports or odds."""
        if not 1 <= limit <= 100 or request.sport is None or request.market != "h2h":
            raise TheOddsApiConfigurationError("discovery requires a sport, h2h and limit 1..100")
        self._validate_request(request.model_copy(update={"event_id": "discovery"}))
        query = urlencode({"apiKey": self._configured_api_key(), "dateFormat": "iso"})
        url = f"{_BASE_URL}/sports/{quote(request.sport, safe='')}/events?{query}"
        try:
            status, _, response = self._get(url)
        except HTTPError as error:
            raise _http_status_error(error.code) from None
        except (URLError, OSError):
            raise TheOddsApiTransportError("The Odds API discovery failed") from None
        if status != 200:
            raise _http_status_error(status)
        try:
            payload = json.loads(response)
            if not isinstance(payload, list) or len(payload) > 1000:
                raise TheOddsApiPayloadError("The Odds API event list is invalid or oversized")
            events: dict[str, DiscoveredEvent] = {}
            now = _ensure_aware(self._clock(), "clock result")
            for item in payload:
                if not isinstance(item, dict):
                    raise TheOddsApiPayloadError("The Odds API event is invalid")
                event = DiscoveredEvent.model_validate(
                    {
                        "event_id": _required_text(item, "id"),
                        "sport": _required_text(item, "sport_key"),
                        "starts_at": _required_text(item, "commence_time"),
                    }
                )
                if event.sport != request.sport:
                    raise TheOddsApiPayloadError("The Odds API discovery sport mismatch")
                if event.event_id in events and events[event.event_id] != event:
                    raise TheOddsApiPayloadError("The Odds API conflicting event identity")
                events[event.event_id] = event
            return tuple(
                sorted(
                    events.values(), key=lambda e: (e.starts_at <= now, e.starts_at, e.event_id)
                )[:limit]
            )
        except (ValidationError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TheOddsApiPayloadError("The Odds API discovery payload invalid") from error

    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        return self.fetch(request)

    def fetch(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        self._validate_request(request)
        event = self._fetch_event(request, self._configured_api_key())
        fetched_at = _ensure_aware(self._clock(), "clock result")
        return self._normalize_event(request, event, fetched_at)

    def _validate_request(self, request: DataCollectionRequest) -> None:
        if request.source.transport is not SourceTransport.API:
            raise TheOddsApiConfigurationError("The Odds API source must use API transport")
        if request.source.provider_id != THE_ODDS_API_PROVIDER_ID:
            raise TheOddsApiConfigurationError("request source is not The Odds API")
        if request.sport is None or request.event_id is None or request.market is None:
            raise TheOddsApiConfigurationError(
                "The Odds API collection requires sport, event_id, and market"
            )
        if request.market.endswith("_lay"):
            raise TheOddsApiConfigurationError(
                "The Odds API lay markets are not supported by this adapter"
            )

    def _configured_api_key(self) -> str:
        value = self._api_key or os.environ.get("QBET_THE_ODDS_API_KEY")
        if value is None or not value.strip():
            raise TheOddsApiConfigurationError("QBET_THE_ODDS_API_KEY is not configured")
        return value

    def _fetch_event(self, request: DataCollectionRequest, api_key: str) -> Mapping[str, Any]:
        assert request.sport is not None
        assert request.event_id is not None
        assert request.market is not None
        query = urlencode(
            {
                "apiKey": api_key,
                "regions": self._region,
                "markets": request.market,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            }
        )
        sport = quote(request.sport, safe="")
        event_id = quote(request.event_id, safe="")
        url = f"{_BASE_URL}/sports/{sport}/events/{event_id}/odds?{query}"
        try:
            status, _, response = self._get(url)
        except HTTPError as error:
            raise _http_status_error(error.code) from None
        except (URLError, OSError):
            raise TheOddsApiTransportError("The Odds API request failed") from None
        if status != 200:
            raise _http_status_error(status)
        try:
            payload = json.loads(response)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TheOddsApiPayloadError("The Odds API returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise TheOddsApiPayloadError("The Odds API event response must be an object")
        return payload

    def _normalize_event(
        self,
        request: DataCollectionRequest,
        event: Mapping[str, Any],
        fetched_at: datetime,
    ) -> NormalizedMarketSnapshot:
        try:
            assert request.sport is not None
            assert request.event_id is not None
            assert request.market is not None
            event_id = _required_text(event, "id")
            if event_id != request.event_id:
                raise TheOddsApiPayloadError(
                    "The Odds API event identity did not match the request"
                )
            sport = _required_text(event, "sport_key")
            if sport != request.sport:
                raise TheOddsApiPayloadError(
                    "The Odds API sport identity did not match the request"
                )
            market_id = f"{event_id}:{request.market}"
            offers = _offers_for_market(
                event=event,
                market_key=request.market,
                market_id=market_id,
                currency=self._currency,
                available_stake=self._available_stake,
                observed_at=fetched_at,
            )
            return NormalizedMarketSnapshot(
                id=market_id,
                correlation_id=request.correlation_id,
                target=request.target,
                source=request.source,
                sport=sport,
                event_id=event_id,
                market_id=market_id,
                fetched_at=fetched_at,
                freshness=(
                    FreshnessStatus.UNKNOWN
                    if any(offer.source_updated_at is None for offer in offers)
                    else FreshnessStatus.STALE
                    if any(
                        offer.source_updated_at is not None
                        and not timedelta()
                        <= fetched_at - offer.source_updated_at
                        <= self._freshness_window
                        for offer in offers
                    )
                    else FreshnessStatus.FRESH
                ),
                completeness=CompletenessStatus.COMPLETE,
                offers=offers,
            )
        except ValidationError as error:
            raise TheOddsApiPayloadError("The Odds API payload validation failed") from error


def _http_status_error(status: int) -> TheOddsApiError:
    message = f"The Odds API request failed with HTTP {status}"
    if status in {401, 403}:
        return TheOddsApiAuthenticationError(message)
    if status == 429:
        return TheOddsApiRateLimitError(message)
    return TheOddsApiTransportError(message)


def _offers_for_market(
    *,
    event: Mapping[str, Any],
    market_key: str,
    market_id: str,
    currency: Currency,
    available_stake: Decimal,
    observed_at: datetime,
) -> tuple[NormalizedOffer, ...]:
    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list) or not bookmakers:
        raise TheOddsApiPayloadError("The Odds API response has no bookmaker data")
    offers: list[NormalizedOffer] = []
    catalog = load_german_sportsbook_catalog()
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            raise TheOddsApiPayloadError("The Odds API bookmaker data is invalid")
        bookmaker_key = _required_text(bookmaker, "key")
        resolution = catalog.resolve(source_id=THE_ODDS_API_PROVIDER_ID, external_key=bookmaker_key)
        markets = bookmaker.get("markets")
        if not isinstance(markets, list):
            raise TheOddsApiPayloadError("The Odds API bookmaker markets are invalid")
        matching_markets = [
            market
            for market in markets
            if isinstance(market, dict) and market.get("key") == market_key
        ]
        if len(matching_markets) != 1:
            raise TheOddsApiPayloadError("The Odds API response lacks the requested market")
        market = matching_markets[0]
        source_time = market.get("last_update")
        if source_time is None:
            source_updated_at = None
        else:
            try:
                source_updated_at = _ensure_aware(
                    datetime.fromisoformat(str(source_time).replace("Z", "+00:00")),
                    "source update",
                )
            except ValueError as error:
                raise TheOddsApiPayloadError("The Odds API source timestamp is invalid") from error
        outcomes = market.get("outcomes")
        if not isinstance(outcomes, list) or len(outcomes) < 2:
            raise TheOddsApiPayloadError("The Odds API market must contain at least two outcomes")
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                raise TheOddsApiPayloadError("The Odds API market outcome is invalid")
            selection = _required_text(outcome, "name")
            offers.append(
                NormalizedOffer(
                    id=f"{bookmaker_key}:{selection}",
                    market_id=market_id,
                    selection=selection,
                    provider=bookmaker_key,
                    side=OfferSide.BACK,
                    odds=_decimal_odds(outcome.get("price")),
                    available_stake=available_stake,
                    currency=currency,
                    availability=OfferAvailability.AVAILABLE,
                    observed_at=observed_at,
                    source_updated_at=source_updated_at,
                    stake_capacity_basis="simulation_assumed",
                    canonical_provider_id=(
                        resolution.provider.provider_id if resolution.provider is not None else None
                    ),
                    provider_mapping_status=resolution.reason.value,
                    licensed_catalog_presence=resolution.eligible,
                )
            )
    if not offers:
        raise TheOddsApiPayloadError("The Odds API response has no usable offers")
    return tuple(offers)


def _required_text(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise TheOddsApiPayloadError(f"The Odds API field {field} is required")
    return item


def _decimal_odds(value: Any) -> Decimal:
    try:
        odds = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise TheOddsApiPayloadError("The Odds API outcome price is invalid") from error
    if odds.is_nan() or odds.is_infinite() or odds <= 1:
        raise TheOddsApiPayloadError(
            "The Odds API outcome price must be finite and greater than one"
        )
    return odds


def _ensure_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TheOddsApiPayloadError(f"{name} must include timezone information")
    return value


def _default_http_get(url: str) -> tuple[int, Mapping[str, str], bytes]:
    with urlopen(url, timeout=15) as response:
        return response.status, dict(response.headers.items()), response.read(2_000_001)
