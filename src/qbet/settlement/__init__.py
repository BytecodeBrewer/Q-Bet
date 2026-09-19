"""Validated settlement is the only dispatch-result capital writer."""

from qbet.data.results import NormalizedMatchResult, ResultCollectionOutcome
from qbet.domain.ledger import LedgerCommand, LedgerOperation
from qbet.execution.models import ExecutionRecord, Lifecycle, SandboxResult
from qbet.execution.sandbox import SandboxRequestHandler
from qbet.ledger import PortfolioLedger


def ledger_command(record: ExecutionRecord, operation: LedgerOperation, amount) -> LedgerCommand:
    work = record.proposal.work
    return LedgerCommand(
        id=f"{work.id}:{operation.value}",
        dispatch_id=str(work.id),
        correlation_id=str(work.correlation_id),
        currency=record.proposal.currency,
        operation=operation,
        amount=amount,
    )


def transition(record: ExecutionRecord, state: Lifecycle, **changes) -> ExecutionRecord:
    return ExecutionRecord.model_validate(
        {
            **record.model_dump(),
            "state": state,
            "transitions": (*record.transitions, state),
            **changes,
        }
    )


class SettlementService:
    _PROVIDER_TO_SANDBOX_STATUS = {
        "success": "success",
        "failed": "failed",
        "cancelled": "cancelled",
    }

    @staticmethod
    def collected_result_for_settlement(
        outcome: ResultCollectionOutcome,
    ) -> NormalizedMatchResult:
        """Admit only a validated available collection result to post-event settlement."""

        return outcome.require_result_for_settlement_or_reporting()

    @classmethod
    def validates_collected_result(
        cls,
        record: ExecutionRecord,
        outcome: ResultCollectionOutcome,
    ) -> NormalizedMatchResult:
        """Require a collected result to belong to this execution before settlement."""

        result = cls.collected_result_for_settlement(outcome)
        work = record.proposal.work
        if (
            outcome.request.match_id != work.opportunity_id
            or outcome.request.execution_id != str(work.id)
            or outcome.request.correlation_id != work.correlation_id
            or outcome.request.source != result.source
            or result.match_id != outcome.request.match_id
            or result.execution_id != outcome.request.execution_id
            or result.correlation_id != outcome.request.correlation_id
            or result.source != outcome.request.source
            or result.provider_target != outcome.request.provider_target
        ):
            raise ValueError("collected_result_identity_mismatch")
        if (
            result.match_id != work.opportunity_id
            or result.execution_id != str(work.id)
            or result.correlation_id != work.correlation_id
        ):
            raise ValueError("collected_result_identity_mismatch")
        proposal = record.proposal
        if proposal.result_source is not None and (
            outcome.request.source != proposal.result_source
            or outcome.request.provider_target != proposal.result_provider_target
        ):
            raise ValueError("collected_result_provider_identity_mismatch")
        return result

    @classmethod
    def _result_for_settlement(
        cls,
        result: SandboxResult,
        collected_result: ResultCollectionOutcome | None,
    ) -> SandboxResult:
        if collected_result is None:
            return result
        normalized = cls.collected_result_for_settlement(collected_result)
        if normalized.provider_outcome is None:
            # Event finality/score evidence is post-event data and must not rewrite
            # the dispatch acknowledgement timestamp or financial result status.
            return result
        mapped = cls._PROVIDER_TO_SANDBOX_STATUS.get(normalized.provider_outcome)
        if mapped is None:
            raise ValueError("collected_result_outcome_unmapped")
        return result.model_copy(update={"status": mapped, "observed_at": normalized.observed_at})

    def settle(
        self,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
        result: SandboxResult,
        *,
        collected_result: ResultCollectionOutcome | None = None,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        normalized = (
            self.validates_collected_result(record, collected_result)
            if collected_result is not None
            else None
        )
        settlement_result = self._result_for_settlement(result, collected_result)
        if record.state in {
            Lifecycle.SETTLED,
            Lifecycle.FAILED,
            Lifecycle.CANCELLED,
        }:
            same_collected_result = normalized is None or record.collected_result == normalized
            if record.result == settlement_result and same_collected_result:
                return record, ledger
            return record.model_copy(update={"error": "settlement_result_conflict"}), ledger
        if record.state is not Lifecycle.ACKNOWLEDGED:
            return record.model_copy(update={"error": "invalid_settlement_state"}), ledger
        reason = SandboxRequestHandler().validate_result(record.proposal, settlement_result)
        if reason:
            return record.model_copy(update={"error": reason}), ledger
        if ledger.balance.mode != record.proposal.work.mode.value:
            return record.model_copy(update={"error": "ledger_mode_mismatch"}), ledger
        operation = (
            LedgerOperation.SETTLE if settlement_result.status == "success" else LedgerOperation.FAIL
        )
        amount = (
            settlement_result.payout
            if settlement_result.status == "success"
            else record.proposal.capital_required
        )
        updated, decision = ledger.apply(ledger_command(record, operation, amount))
        if not decision.accepted:
            return record.model_copy(update={"error": decision.reason}), ledger
        state = {
            "success": Lifecycle.SETTLED,
            "failed": Lifecycle.FAILED,
            "cancelled": Lifecycle.CANCELLED,
        }[settlement_result.status]
        return transition(
            record,
            state,
            result=settlement_result,
            collected_result=normalized,
            error=None,
        ), updated