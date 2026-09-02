"""Typed, read-only exports for persisted simulation reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

from pydantic import BaseModel

from qbet.reporting import ReportDetailSelection, SimulationReport

_REPORT_DETAIL_FIELDS: Final = (
    ("events", "include_events"),
    ("intermediate_results", "include_intermediate_results"),
    ("raw_input_snapshots", "include_raw_inputs"),
    ("warnings", "include_warnings"),
    ("errors", "include_errors"),
    ("risk_decisions", "include_risk_decisions"),
    ("workflow_transitions", "include_workflow_transitions"),
)


@dataclass(frozen=True)
class ReportExportSection:
    """One explicitly selected, persisted report detail section."""

    name: str
    value: object


@dataclass(frozen=True)
class SimulationReportExport:
    """Stable export shape shared by CSV and JSON report downloads."""

    run_id: UUID
    engine: str
    mode: str
    status: str
    progress: Decimal
    starting_capital: Decimal
    current_capital: Decimal
    profit_loss: Decimal
    completed_steps: int
    generated_at: datetime
    details: tuple[ReportExportSection, ...]

    @classmethod
    def from_report(
        cls, report: SimulationReport, selection: ReportDetailSelection
    ) -> SimulationReportExport:
        details = tuple(
            ReportExportSection(name=name, value=getattr(report, name))
            for name, selection_field in _REPORT_DETAIL_FIELDS
            if getattr(selection, selection_field)
        )
        return cls(
            run_id=report.run_id,
            engine=str(report.engine),
            mode="simulation",
            status=report.status.value,
            progress=report.progress,
            starting_capital=report.starting_capital,
            current_capital=report.current_capital,
            profit_loss=report.profit_loss,
            completed_steps=len(report.completed_steps),
            generated_at=report.generated_at,
            details=details,
        )

    def json_document(self) -> dict[str, object]:
        """Return the documented, JSON-safe export representation."""

        document: dict[str, object] = {
            "run_id": str(self.run_id),
            "engine": self.engine,
            "mode": self.mode,
            "status": self.status,
            "progress": _json_ready(self.progress),
            "starting_capital": _json_ready(self.starting_capital),
            "current_capital": _json_ready(self.current_capital),
            "profit_loss": _json_ready(self.profit_loss),
            "completed_steps": self.completed_steps,
            "generated_at": self.generated_at.isoformat(),
            "details": {
                detail.name: _json_ready(detail.value) for detail in self.details
            },
        }
        return document

    def csv_rows(self) -> tuple[tuple[str, str], ...]:
        """Return labelled CSV rows from the same typed export model."""

        rows = (
            ("run_id", str(self.run_id)),
            ("engine", self.engine),
            ("mode", self.mode),
            ("status", self.status),
            ("progress", str(self.progress)),
            ("starting_capital", str(self.starting_capital)),
            ("current_capital", str(self.current_capital)),
            ("profit_loss", str(self.profit_loss)),
            ("completed_steps", str(self.completed_steps)),
            ("generated_at", self.generated_at.isoformat()),
        )
        return rows + tuple(
            (
                f"detail.{detail.name}",
                json.dumps(_json_ready(detail.value), sort_keys=True),
            )
            for detail in self.details
        )


def _json_ready(value: object) -> object:
    return json.loads(json.dumps(value, default=_json_default))


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Unsupported report export value: {type(value)!r}")
