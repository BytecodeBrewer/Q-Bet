from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qbet.simulation import (
    AlphaSimulationAdapter,
    DeterministicSimulationRunner,
    SimulationEngine,
    SimulationEventType,
    SimulationRunConfig,
    SimulationStatus,
    SimulationStep,
    SimulationTopUpEvent,
    YieldSimulationAdapter,
)


def config(**overrides: object) -> SimulationRunConfig:
    values: dict[str, object] = {
        "engine": SimulationEngine.BONUS,
        "starting_capital": Decimal(100),
    }
    values.update(overrides)
    return SimulationRunConfig.model_validate(values)


def steps() -> tuple[SimulationStep, ...]:
    return (
        SimulationStep(id="first", capital_change=Decimal(10)),
        SimulationStep(id="second", capital_change=Decimal(-5)),
        SimulationStep(id="third", capital_change=Decimal(2)),
    )


def test_runner_is_reproducible_for_identical_configurations_and_steps() -> None:
    first = DeterministicSimulationRunner().run(config(), steps())
    second = DeterministicSimulationRunner().run(config(), steps())

    assert first == second
    assert first.status == SimulationStatus.COMPLETED
    assert first.current_capital == Decimal(107)
    assert first.progress == Decimal(1)
    assert tuple(event.sequence for event in first.events) == tuple(range(1, 6))


def test_runner_exposes_running_context_and_completed_status() -> None:
    observed_contexts = []
    runner = DeterministicSimulationRunner()

    runner.run(config(), steps()[:1], on_step_completed=observed_contexts.append)

    assert observed_contexts[0].status == SimulationStatus.RUNNING
    assert observed_contexts[0].completed_step_count == 1
    assert observed_contexts[0].current_capital == Decimal(110)
    assert runner.status == SimulationStatus.COMPLETED
    assert runner.completed_steps == steps()[:1]
    assert runner.current_capital == Decimal(110)


def test_stop_request_keeps_the_completed_step_and_its_scheduled_top_up() -> None:
    runner = DeterministicSimulationRunner()

    result = runner.run(
        config(top_up_events=(SimulationTopUpEvent(after_completed_steps=1, amount=Decimal(20)),)),
        steps(),
        on_step_completed=lambda _: runner.request_stop(),
    )

    assert result.status == SimulationStatus.STOPPED
    assert result.completed_steps == steps()[:1]
    assert result.current_capital == Decimal(130)
    assert result.progress == Decimal(1) / Decimal(3)
    assert [event.event_type for event in result.events] == [
        SimulationEventType.RUN_STARTED,
        SimulationEventType.STEP_COMPLETED,
        SimulationEventType.TOP_UP_APPLIED,
        SimulationEventType.STOP_REQUESTED,
        SimulationEventType.RUN_STOPPED,
    ]


def test_top_ups_are_simulation_only_events() -> None:
    result = DeterministicSimulationRunner().run(
        config(top_up_events=(SimulationTopUpEvent(after_completed_steps=0, amount=Decimal(25)),)),
        (),
    )

    assert result.current_capital == Decimal(125)
    assert result.status == SimulationStatus.COMPLETED
    assert [event.event_type for event in result.events] == [
        SimulationEventType.RUN_STARTED,
        SimulationEventType.TOP_UP_APPLIED,
        SimulationEventType.RUN_COMPLETED,
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("engine", "unsupported"),
        ("starting_capital", Decimal(0)),
        ("max_duration", timedelta(0)),
        ("max_duration", timedelta(hours=49)),
        (
            "top_up_events",
            (SimulationTopUpEvent(after_completed_steps=0, amount=Decimal(1)), "bad"),
        ),
    ],
)
def test_rejects_invalid_configuration(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        config(**{field: value})


@pytest.mark.parametrize(
    "adapter, engine",
    [
        (YieldSimulationAdapter(), SimulationEngine.YIELD),
        (AlphaSimulationAdapter(), SimulationEngine.ALPHA),
    ],
)
def test_placeholder_adapters_share_the_runner_contract(
    adapter: object, engine: SimulationEngine
) -> None:
    typed_adapter = adapter
    run_config = config(engine=engine)
    placeholder_steps = typed_adapter.build_steps(run_config)  # type: ignore[union-attr]

    result = DeterministicSimulationRunner().run(run_config, placeholder_steps)

    assert typed_adapter.is_placeholder is True  # type: ignore[union-attr]
    assert result.status == SimulationStatus.COMPLETED
    assert result.current_capital == run_config.starting_capital
    assert result.completed_steps[0].id == f"{engine}-placeholder"


def test_rejects_step_that_would_make_simulated_capital_negative() -> None:
    with pytest.raises(ValueError, match="simulated capital negative"):
        DeterministicSimulationRunner().run(
            config(starting_capital=Decimal(10)),
            (SimulationStep(id="loss", capital_change=Decimal(-11)),),
        )


def test_stops_before_a_step_would_exceed_max_duration() -> None:
    result = DeterministicSimulationRunner().run(
        config(max_duration=timedelta(hours=1)),
        (
            SimulationStep(
                id="within-limit",
                capital_change=Decimal(10),
                simulated_duration=timedelta(minutes=30),
            ),
            SimulationStep(
                id="beyond-limit",
                capital_change=Decimal(50),
                simulated_duration=timedelta(minutes=45),
            ),
        ),
    )

    assert result.status == SimulationStatus.STOPPED
    assert tuple(step.id for step in result.completed_steps) == ("within-limit",)
    assert result.current_capital == Decimal(110)
    assert result.elapsed_duration == timedelta(minutes=30)
    assert result.progress == Decimal("0.5")
    assert [event.event_type for event in result.events] == [
        SimulationEventType.RUN_STARTED,
        SimulationEventType.STEP_COMPLETED,
        SimulationEventType.DURATION_LIMIT_REACHED,
        SimulationEventType.RUN_STOPPED,
    ]


def test_stops_cleanly_when_a_large_duration_would_overflow_comparison() -> None:
    result = DeterministicSimulationRunner().run(
        config(max_duration=timedelta(hours=1)),
        (
            SimulationStep(
                id="elapsed",
                capital_change=Decimal(1),
                simulated_duration=timedelta(minutes=1),
            ),
            SimulationStep(
                id="too-large",
                capital_change=Decimal(1),
                simulated_duration=timedelta.max,
            ),
        ),
    )

    assert result.status == SimulationStatus.STOPPED
    assert tuple(step.id for step in result.completed_steps) == ("elapsed",)
    assert result.elapsed_duration == timedelta(minutes=1)
    assert [event.event_type for event in result.events][-2:] == [
        SimulationEventType.DURATION_LIMIT_REACHED,
        SimulationEventType.RUN_STOPPED,
    ]


@pytest.mark.parametrize("capital_change", [Decimal("Infinity"), Decimal("NaN")])
def test_rejects_non_finite_capital_changes(capital_change: Decimal) -> None:
    with pytest.raises(ValidationError):
        SimulationStep(id="invalid-change", capital_change=capital_change)


@pytest.mark.parametrize("amount", [Decimal("Infinity"), Decimal("NaN")])
def test_rejects_non_finite_initial_and_top_up_capital(amount: Decimal) -> None:
    with pytest.raises(ValidationError):
        config(starting_capital=amount)

    with pytest.raises(ValidationError):
        SimulationTopUpEvent(after_completed_steps=0, amount=amount)
