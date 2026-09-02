"""Generic contract for concrete strategy engines."""

from typing import Protocol, TypeVar

RequestT_contra = TypeVar("RequestT_contra", contravariant=True)
EvaluationT_co = TypeVar("EvaluationT_co", covariant=True)


class StrategyEngine(Protocol[RequestT_contra, EvaluationT_co]):
    def evaluate(self, request: RequestT_contra) -> EvaluationT_co: ...
