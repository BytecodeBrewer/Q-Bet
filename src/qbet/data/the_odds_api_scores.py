"""Read-only The Odds API score collector for post-event settlement."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from pydantic import ValidationError

from qbet.data.models import SourceTransport
from qbet.data.results import (
    NormalizedMatchResult,
    ResultAvailability,
    ResultCollectionOutcome,
    ResultCollectionRequest,
    ResultCollectionStatus,
    ResultScore,
)
from qbet.data.the_odds_api import (
    THE_ODDS_API_PROVIDER_ID,
    TheOddsApiAuthenticationError,
    TheOddsApiConfigurationError,
    TheOddsApiError,
    TheOddsApiPayloadError,
    TheOddsApiRateLimitError,
    TheOddsApiTransportError,
)

_BASE_URL = "https://api.the-odds-api.com/v4"
HttpGet = Callable[[str], tuple[int, Mapping[str, str], bytes]]


class TheOddsApiScoreCollector:
    """Fetch one exact event score and normalize it without settlement side effects."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        days_from: int = 3,
        http_get: HttpGet | None = None,
    ) -> None:
        if not 1 <= days_from <= 3:
            raise ValueError("days_from must be between 1 and 3")
        self._api_key = api_key
        self._days_from = days_from
        self._http_get = http_get or _default_http_get

    def collect(self, request: ResultCollectionRequest) -> ResultCollectionOutcome:
        try:
            self._validate_request(request)
            payload = self._fetch_scores(request, self._configured_api_key())
            return self._normalize(request, payload)
        except TheOddsApiAuthenticationError:
            return _outcome(request, ResultCollectionStatus.FAILED, "result_provider_auth_failed")
        except TheOddsApiConfigurationError:
            return _outcome(
                request,
                ResultCollectionStatus.FAILED,
                "result_provider_configuration_invalid",
            )
        except TheOddsApiRateLimitError:
            return _outcome(request, ResultCollectionStatus.UNKNOWN, "result_provider_rate_limited")
        except TheOddsApiTransportError:
            return _outcome(
                request,
                ResultCollectionStatus.UNKNOWN,
                "result_provider_unavailable",
            )
        except TheOddsApiPayloadError:
            return _outcome(request, ResultCollectionStatus.INVALID, "result_provider_payload_invalid")
        except TheOddsApiError:
            return _outcome(
                request,
                ResultCollectionStatus.UNKNOWN,
                "result_provider_unavailable",
            )

    def _validate_request(self, request: ResultCollectionRequest) -> None:
        if request.source.transport is not SourceTransport.API:
            raise TheOddsApiConfigurationError("The Odds API score source must use API transport")
        if request.source.provider_id != THE_ODDS_API_PROVIDER_ID:
            raise TheOddsApiConfigurationError("result source is not The Odds API")
        if request.provider_target is None:
            raise TheOddsApiConfigurationError("result provider target is required")

    def _configured_api_key(self) -> str:
        value = self._api_key or os.environ.get("QBET_THE_ODDS_API_KEY")
        if value is None or not value.strip():
            raise TheOddsApiConfigurationError("QBET_THE_ODDS_API_KEY is not configured")
        return value

    def _fetch_scores(
        self,
        request: ResultCollectionRequest,
        api_key: str,
    ) -> list[Any]:
        target = request.provider_target
        assert target is not None
        query = urlencode(
            {
                "apiKey": api_key,
                "daysFrom": self._days_from,
                "dateFormat": "iso",
                "eventIds": target.event_id,
            }
        )
        sport = quote(target.sport, safe="")
        url = f"{_BASE_URL}/sports/{sport}/scores/?{query}"
        try:
            status, _, response = self._http_get(url)
        except HTTPError as error:
            raise _http_status_error(error.code) from None
        except (URLError, OSError):
            raise TheOddsApiTransportError("The Odds API score request failed") from None
        if status != 200:
            raise _http_status_error(status)
        try:
            payload = json.loads(response)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TheOddsApiPayloadError("The Odds API returned invalid score JSON") from error
        if not isinstance(payload, list):
            raise TheOddsApiPayloadError("The Odds API score response must be a list")
        return payload

    def _normalize(
        self,
        request: ResultCollectionRequest,
        payload: list[Any],
    ) -> ResultCollectionOutcome:
        target = request.provider_target
        assert target is not None
        if not payload:
            return _outcome(
                request,
                ResultCollectionStatus.NOT_YET_AVAILABLE,
                "result_event_not_found",
            )
        objects = [item for item in payload if isinstance(item, dict)]
        if len(objects) != len(payload):
            raise TheOddsApiPayloadError("The Odds API score item is invalid")
        matching = [item for item in objects if item.get("id") == target.event_id]
        if not matching:
            return _outcome(
                request,
                ResultCollectionStatus.MISMATCHED,
                "result_event_identity_mismatch",
            )
        if len(matching) != 1:
            raise TheOddsApiPayloadError("The Odds API returned duplicate score events")
        event = matching[0]
        sport = _required_text(event, "sport_key")
        if sport != target.sport:
            return _outcome(
                request,
                ResultCollectionStatus.MISMATCHED,
                "result_sport_identity_mismatch",
            )
        completed = event.get("completed")
        if not isinstance(completed, bool):
            raise TheOddsApiPayloadError("The Odds API completed flag is invalid")
        raw_scores = event.get("scores")
        if not completed:
            if raw_scores is None:
                return _outcome(
                    request,
                    ResultCollectionStatus.NOT_YET_AVAILABLE,
                    "result_not_yet_available",
                )
            scores = _scores(raw_scores)
            if not scores:
                return _outcome(
                    request,
                    ResultCollectionStatus.NOT_YET_AVAILABLE,
                    "result_not_yet_available",
                )
            return _outcome(
                request,
                ResultCollectionStatus.PARTIAL,
                "result_in_progress",
            )

        observed_at = _iso_datetime(event.get("last_update"))
        scores = _scores(raw_scores)
        if len(scores) < 2:
            raise TheOddsApiPayloadError("completed score result requires at least two scores")
        if observed_at < request.fresh_after:
            return _outcome(request, ResultCollectionStatus.STALE, "result_stale")
        try:
            result = NormalizedMatchResult(
                match_id=request.match_id,
                execution_id=request.execution_id,
                correlation_id=request.correlation_id,
                source=request.source,
                availability=ResultAvailability.AVAILABLE,
                observed_at=observed_at,
                provider_target=target,
                completed=True,
                scores=scores,
            )
            return ResultCollectionOutcome(
                request=request,
                status=ResultCollectionStatus.AVAILABLE,
                result=result,
            )
        except ValidationError as error:
            raise TheOddsApiPayloadError("The Odds API score payload validation failed") from error


def _scores(value: Any) -> tuple[ResultScore, ...]:
    if not isinstance(value, list):
        raise TheOddsApiPayloadError("The Odds API scores field is invalid")
    parsed: list[ResultScore] = []
    for item in value:
        if not isinstance(item, dict):
            raise TheOddsApiPayloadError("The Odds API score entry is invalid")
        participant = _required_text(item, "name")
        try:
            score = Decimal(str(item.get("score")))
        except (InvalidOperation, ValueError) as error:
            raise TheOddsApiPayloadError("The Odds API score value is invalid") from error
        if score.is_nan() or score.is_infinite() or score < 0:
            raise TheOddsApiPayloadError("The Odds API score value must be non-negative")
        parsed.append(ResultScore(participant=participant, score=score))
    if len({score.participant for score in parsed}) != len(parsed):
        raise TheOddsApiPayloadError("The Odds API score participants must be distinct")
    return tuple(parsed)


def _required_text(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise TheOddsApiPayloadError(f"The Odds API score field {field} is required")
    return item


def _iso_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TheOddsApiPayloadError("The Odds API last_update field is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TheOddsApiPayloadError("The Odds API last_update field is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TheOddsApiPayloadError("The Odds API last_update must include timezone information")
    return parsed


def _http_status_error(status: int) -> TheOddsApiError:
    message = f"The Odds API score request failed with HTTP {status}"
    if status in {401, 403}:
        return TheOddsApiAuthenticationError(message)
    if status == 429:
        return TheOddsApiRateLimitError(message)
    if status == 404:
        return TheOddsApiConfigurationError(message)
    return TheOddsApiTransportError(message)


def _outcome(
    request: ResultCollectionRequest,
    status: ResultCollectionStatus,
    reason_code: str,
) -> ResultCollectionOutcome:
    return ResultCollectionOutcome(request=request, status=status, reason_code=reason_code)


def _default_http_get(url: str) -> tuple[int, Mapping[str, str], bytes]:
    with urlopen(url, timeout=15) as response:
        return response.status, dict(response.headers.items()), response.read()