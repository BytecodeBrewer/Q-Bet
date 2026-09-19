from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from qbet.data.results import NormalizedMatchResult
from qbet.domain.ledger import FiniteBalance
from qbet.domain.models import Currency, DomainModel
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.workflow.routing import RoutedWorkItem


class Lifecycle(StrEnum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class ExecutionProposal(DomainModel):
    work: RoutedWorkItem
    request: BonusEngineRequest | SportsCapitalEngineRequest
    expires_at: AwareDatetime
    currency: Currency
    capital_required: FiniteBalance
    payout: FiniteBalance


class ApprovedExecutionRequest(DomainModel):
    proposal: ExecutionProposal
    approved_by: str = Field(min_length=1)
    approved_at: AwareDatetime
    notification_email: str = ""
    notification_display_name: str = ""


class SandboxResult(DomainModel):
    dispatch_id: UUID
    correlation_id: UUID
    mode: Literal["simulation", "execution"]
    currency: Currency
    payout: FiniteBalance
    status: str
    observed_at: AwareDatetime


class ExecutionRecord(DomainModel):
    proposal: ExecutionProposal
    state: Lifecycle = Lifecycle.AWAITING_APPROVAL
    transitions: tuple[Lifecycle, ...] = (Lifecycle.PROPOSED, Lifecycle.AWAITING_APPROVAL)
    approval: ApprovedExecutionRequest | None = None
    result: SandboxResult | None = None
    collected_result: NormalizedMatchResult | None = None
    error: str | None = None