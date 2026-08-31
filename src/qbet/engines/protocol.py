"""Generic contract for concrete strategy engines."""

from typing import Protocol, TypeVar

RequestT = TypeVar("RequestT", contravariant=True)
EvaluationT = TypeVar("EvaluationT", covariant=True)


class StrategyEngine(Protocol[RequestT, EvaluationT]):
    def evaluate(self, request: RequestT) -> EvaluationT: ...
