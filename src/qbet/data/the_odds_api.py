"""Read-only The Odds API adapter for normalized market snapshots."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from qbet.data.models import (
    CompletenessStatus,
    DataCollectionRequest,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
)
from qbet.domain.models import Currency, OfferSide

THE_ODDS_API_PROVIDER_ID = "the_odds_api"
_BASE_URL = "https://api.the-odds-api.com/v4"

HttpGet = Callable[[str], tuple[int, Mapping[str, str], bytes]]
Clock = Callable[[], datetime]


class TheOddsApiError(RuntimeError):
    """A read-only provider failure that does not expose credentials."""


class TheOddsApiConfigurationError(TheOddsApiError):
    """Raised when required application-bound configuration is missing."""


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
        max_snapshot_age: timedelta = timedelta(minutes=5),
        http_get: HttpGet | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not region.strip():
            raise ValueError("region must not be blank")
        if available_stake.is_nan() or available_stake.is_infinite() or available_stake < 0:
            raise ValueError("available_stake must be a finite non-negative decimal")
        if max_snapshot_age < timedelta():
            raise ValueError("max_snapshot_age must not be negative")
        self._api_key = api_key
        self._region = region
        self._currency: Currency = currency
        self._available_stake = available_stake
        self._max_snapshot_age = max_snapshot_age
        self._http_get = http_get or _default_http_get
        self._clock = clock or (lambda: datetime.now(UTC))

    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        return self.fetch(request)

    def fetch(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        self._validate_request(request)
        event = self._fetch_event(request, self._configured_api_key())
        return self._normalize_event(request, event)

    def _validate_request(self, request: DataCollectionRequest) -> None:
        if request.source.transport is not SourceTransport.API:
            raise TheOddsApiConfigurationError("The Odds API source must use API transport")
        if request.source.provider_id != THE_ODDS_API_PROVIDER_ID:
            raise TheOddsApiConfigurationError("request source is not The Odds API")
        if request.sport is None or request.event_id is None or request.market is None:
            raise TheOddsApiConfigurationError(
                "The Odds API collection requires sport, event_id, and market"
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
        url = f"{_BASE_URL}/sports/{request.sport}/events/{request.event_id}/odds?{query}"
        try:
            status, _, response = self._http_get(url)
        except (HTTPError, URLError, OSError) as error:
            raise TheOddsApiError("The Odds API request failed") from error
        if status != 200:
            raise TheOddsApiError(f"The Odds API returned HTTP {status}")
        try:
            payload = json.loads(response)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TheOddsApiPayloadError("The Odds API returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise TheOddsApiPayloadError("The Odds API event response must be an object")
        return payload

    def _normalize_event(
        self, request: DataCollectionRequest, event: Mapping[str, Any]
    ) -> NormalizedMarketSnapshot:
        assert request.sport is not None
        assert request.event_id is not None
        assert request.market is not None
        event_id = _required_text(event, "id")
        if event_id != request.event_id:
            raise TheOddsApiPayloadError("The Odds API event identity did not match the request")
        sport = _required_text(event, "sport_key")
        if sport != request.sport:
            raise TheOddsApiPayloadError("The Odds API sport identity did not match the request")
        fetched_at = _ensure_aware(self._clock(), "clock result")
        market_id = f"{event_id}:{request.market}"
        offers = _offers_for_market(
            event=event,
            market_key=request.market,
            market_id=market_id,
            currency=self._currency,
            available_stake=self._available_stake,
        )
        observed_at = max(offer.observed_at for offer in offers)
        freshness = (
            FreshnessStatus.FRESH
            if fetched_at - observed_at <= self._max_snapshot_age
            else FreshnessStatus.STALE
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
            freshness=freshness,
            completeness=CompletenessStatus.COMPLETE,
            offers=offers,
        )


def _offers_for_market(
    *,
    event: Mapping[str, Any],
    market_key: str,
    market_id: str,
    currency: Currency,
    available_stake: Decimal,
) -> tuple[NormalizedOffer, ...]:
    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list) or not bookmakers:
        raise TheOddsApiPayloadError("The Odds API response has no bookmaker data")
    offers: list[NormalizedOffer] = []
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            raise TheOddsApiPayloadError("The Odds API bookmaker data is invalid")
        bookmaker_key = _required_text(bookmaker, "key")
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
        observed_at = _parse_timestamp(_required_text(market, "last_update"))
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


def _parse_timestamp(value: str) -> datetime:
    try:
        return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")), "last_update")
    except ValueError as error:
        raise TheOddsApiPayloadError("The Odds API last_update timestamp is invalid") from error


def _ensure_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TheOddsApiPayloadError(f"{name} must include timezone information")
    return value


def _default_http_get(url: str) -> tuple[int, Mapping[str, str], bytes]:
    with urlopen(url, timeout=15) as response:
        return response.status, dict(response.headers.items()), response.read()
