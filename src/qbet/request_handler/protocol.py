"""Separate contracts prevent Simulation and Execution state from being shared."""

from typing import Protocol

from qbet.request_handler.models import ModeRequest, RequestHandlerResult, RevalidationResult


class SimulationRequestHandler(Protocol):
    def revalidate(self, request: ModeRequest) -> RevalidationResult: ...

    def retrieve_result(self, request: ModeRequest) -> RequestHandlerResult: ...


class ExecutionRequestHandler(Protocol):
    def revalidate(self, request: ModeRequest) -> RevalidationResult: ...

    def retrieve_result(self, request: ModeRequest) -> RequestHandlerResult: ...
