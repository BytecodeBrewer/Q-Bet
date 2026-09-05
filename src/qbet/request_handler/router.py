"""Mode router for RequestHandlers used by the WorkflowOrchestrator."""

from qbet.request_handler.models import (
    ModeRequest,
    RequestHandlerMode,
    RequestHandlerResult,
    RevalidationOutcome,
    RevalidationResult,
)
from qbet.request_handler.protocol import ExecutionRequestHandler, SimulationRequestHandler


class ModeRequestHandlers:
    """Routes refreshes to separate Simulation and Execution handler instances."""

    def __init__(
        self,
        *,
        simulation: SimulationRequestHandler,
        execution: ExecutionRequestHandler,
    ) -> None:
        if simulation is execution:
            raise ValueError("simulation and execution require separate request handler instances")
        self._simulation = simulation
        self._execution = execution

    def revalidate(self, request: ModeRequest) -> RevalidationResult:
        return self._handler_for(request.mode).revalidate(request)

    def retrieve_result(self, request: ModeRequest) -> RequestHandlerResult:
        return self._handler_for(request.mode).retrieve_result(request)

    @staticmethod
    def workflow_decision(result: RevalidationResult) -> tuple[str, str | None]:
        if result.outcome is RevalidationOutcome.VALID:
            return "allow", None
        if result.outcome in {RevalidationOutcome.CHANGED, RevalidationOutcome.UNAVAILABLE}:
            return "recheck", result.reason_code
        return "reject", result.reason_code

    def _handler_for(
        self, mode: RequestHandlerMode
    ) -> SimulationRequestHandler | ExecutionRequestHandler:
        return self._simulation if mode is RequestHandlerMode.SIMULATION else self._execution
