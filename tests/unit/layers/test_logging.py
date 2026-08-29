from uuid import UUID

from qbet.layers import (
    SimulationLogContext,
    SimulationLogRecordType,
)


def test_log_context_assigns_stable_order_and_redacts_sensitive_raw_fields() -> None:
    context = SimulationLogContext(run_id=UUID("12345678-1234-5678-1234-567812345678"))

    first = context.record(
        SimulationLogRecordType.RAW_INPUT,
        "simulation.config",
        {"api_key": "not-for-storage", "capital": "100"},
    )
    second = context.record(SimulationLogRecordType.RUN_STARTED, "simulation.runner")

    assert first.run_id == second.run_id
    assert [record.sequence for record in context.records] == [1, 2]
    assert first.payload == {"api_key": "[redacted]", "capital": "100"}
    assert first.schema_version == 1
    assert first.timestamp.tzinfo is not None
