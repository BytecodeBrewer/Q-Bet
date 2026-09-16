"""Targeted provider-backed market revalidation for controlled Execution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from qbet.data.models import (
    CompletenessStatus,
    DataCollectionRequest,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    OfferAvailability,
)
from qbet.data.the_odds_api import (
    TheOddsApiAdapter,
    TheOddsApiAuthenticationError,
    TheOddsApiConfigurationError,
    TheOddsApiError,
    TheOddsApiPayloadError,
    TheOddsApiRateLimitError,
    TheOddsApiTransportError,
)
from qbet.domain.models import Identifier
from qbet.request_handler.models import (
    ModeRequest,
    RequestHandlerMode,
    RequestHandlerResult,
    RevalidationOutcome,
    RevalidationResult,
)
from qbet.request_handler.protocol import ExecutionRequestHandler

Clock = Callable[[], datetime]


class TargetedMarketProviderError(RuntimeError):
    """Stable provider failure that carries only a safe Q-Bet reason code."""

    def __init__(self, reason_code: Identifier) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class TargetedMarketProvider(Protocol):
    """Provider-neutral seam for refreshing exactly one selected remote market."""

    def refresh(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot: ...


class TheOddsApiTargetedMarketProvider:
    """Translate The Odds API failures into provider-neutral revalidation failures."""

    def __init__(self, adapter: TheOddsApiAdapter) -> None:
        self._adapter = adapter

    def refresh(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        try:
            return self._adapter.collect(request)
        except TheOddsApiAuthenticationError:
            raise TargetedMarketProviderError("market_provider_auth_failed") from None
        except TheOddsApiRateLimitError:
            raise TargetedMarketProviderError("market_provider_rate_limited") from None
        except TheOddsApiConfigurationError:
            raise TargetedMarketProviderError("market_provider_configuration_failed") from None
        except TheOddsApiPayloadError:
            raise TargetedMarketProviderError("market_provider_invalid_payload") from None
        except TheOddsApiTransportError:
            raise TargetedMarketProviderError("market_provider_unavailable") from None
        except TheOddsApiError:
            raise TargetedMarketProviderError("market_provider_unavailable") from None


class ExecutionMarketRequestHandler:
    """Revalidate one selected market and delegate result retrieval unchanged."""

    def __init__(
        self,
        *,
        provider: TargetedMarketProvider,
        result_handler: ExecutionRequestHandler,
        clock: Clock | None = None,
    ) -> None:
        self._provider = provider
        self._result_handler = result_handler
        self._clock = clock or (lambda: datetime.now(UTC))

    def revalidate(self, request: ModeRequest) -> RevalidationResult:
        if request.mode is not RequestHandlerMode.EXECUTION:
            raise ValueError("targeted market revalidation is execution-only")

        validated_at = self._aware_now()
        context = request.market_revalidation
        if context is None:
            return self._result(
                request,
                validated_at,
                RevalidationOutcome.UNAVAILABLE,
                "market_revalidation_context_missing",
            )
        if context.expires_at is not None and validated_at >= context.expires_at:
            return self._result(
                request,
                validated_at,
                RevalidationOutcome.EXPIRED,
                "market_opportunity_expired",
            )

        provider_request = DataCollectionRequest(
            correlation_id=request.correlation_id,
            target=context.target,
            source=context.source,
            sport=context.sport,
            event_id=context.event_id,
            market=context.market,
        )
        try:
            snapshot = self._provider.refresh(provider_request)
        except TargetedMarketProviderError as error:
            return self._result(
                request,
                validated_at,
                RevalidationOutcome.UNAVAILABLE,
                error.reason_code,
            )

        identity_error = self._identity_error(snapshot, provider_request)
        if identity_error is not None:
            return self._result(
                request,
                validated_at,
                RevalidationOutcome.UNAVAILABLE,
                identity_error,
            )
        if (
            snapshot.freshness is not FreshnessStatus.FRESH
            or snapshot.completeness is not CompletenessStatus.COMPLETE
        ):
            return self._result(
                request,
                validated_at,
                RevalidationOutcome.UNAVAILABLE,
                "market_snapshot_not_ready",
            )

        current_offers = {}
        for offer in snapshot.offers:
            key = (offer.provider, offer.selection, offer.side)
            if key in current_offers:
                return self._result(
                    request,
                    validated_at,
                    RevalidationOutcome.UNAVAILABLE,
                    "market_snapshot_ambiguous_offer",
                )
            current_offers[key] = offer

        for expected in context.expected_offers:
            key = (expected.provider, expected.selection, expected.side)
            current = current_offers.get(key)
            if current is None:
                return self._result(
                    request,
                    validated_at,
                    RevalidationOutcome.CHANGED,
                    "market_offer_missing",
                )
            if current.availability is not OfferAvailability.AVAILABLE:
                return self._result(
                    request,
                    validated_at,
                    RevalidationOutcome.REJECTED,
                    "market_offer_unavailable",
                )
            if current.odds != expected.odds:
                return self._result(
                    request,
                    validated_at,
                    RevalidationOutcome.CHANGED,
                    "market_offer_changed",
                )

        return self._result(request, validated_at, RevalidationOutcome.VALID, None)

    def retrieve_result(self, request: ModeRequest) -> RequestHandlerResult:
        if request.mode is not RequestHandlerMode.EXECUTION:
            raise ValueError("targeted market revalidation is execution-only")
        return self._result_handler.retrieve_result(request)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("request handler clock must include timezone information")
        return value

    @staticmethod
    def _identity_error(
        snapshot: NormalizedMarketSnapshot,
        request: DataCollectionRequest,
    ) -> Identifier | None:
        if snapshot.correlation_id != request.correlation_id:
            return "market_snapshot_correlation_mismatch"
        if snapshot.source != request.source:
            return "market_snapshot_source_mismatch"
        if snapshot.target is not request.target:
            return "market_snapshot_target_mismatch"
        if snapshot.sport != request.sport or snapshot.event_id != request.event_id:
            return "market_snapshot_identity_mismatch"
        return None

    @staticmethod
    def _result(
        request: ModeRequest,
        validated_at: datetime,
        outcome: RevalidationOutcome,
        reason_code: Identifier | None,
    ) -> RevalidationResult:
        return RevalidationResult(
            **request.model_dump(),
            validated_at=validated_at,
            outcome=outcome,
            reason_code=reason_code,
        )
