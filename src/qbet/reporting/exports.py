"""Stable CSV, JSON, and PDF exports for customer-facing result reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from qbet.reporting.customer import CustomerResultReport

_CSV_FIELDS: Final = (
    "report_id",
    "mode",
    "match",
    "provider",
    "counterparty_provider",
    "engine",
    "strategy",
    "invested_capital",
    "profit_loss",
    "current_capital",
    "currency",
    "result_state",
    "created_at",
    "completed_at",
    "transaction_id",
)


@dataclass(frozen=True)
class CustomerReportExport:
    """One typed, business-only export projection shared by every output format."""

    report: CustomerResultReport

    def json_document(self) -> dict[str, object]:
        return {
            "report_id": str(self.report.report_id),
            "mode": self.report.mode,
            "match": self.report.match,
            "providers": {
                "primary": self.report.provider,
                "counterparty": self.report.counterparty_provider,
            },
            "engine": self.report.engine,
            "strategy": self.report.strategy,
            "assigned_amounts": [
                {"label": amount.label, "amount": str(amount.amount)}
                for amount in self.report.assigned_amounts
            ],
            "invested_capital": str(self.report.invested_capital),
            "profit_loss": str(self.report.profit_loss),
            "current_capital": str(self.report.current_capital),
            "currency": self.report.currency,
            "result_state": self.report.result_state,
            "created_at": self.report.created_at.isoformat(),
            "completed_at": self.report.completed_at.isoformat(),
            "transaction_id": self.report.transaction_id,
        }

    def csv_rows(self) -> tuple[tuple[str, str], ...]:
        values = {
            "report_id": str(self.report.report_id),
            "mode": self.report.mode,
            "match": self.report.match,
            "provider": self.report.provider,
            "counterparty_provider": self.report.counterparty_provider,
            "engine": self.report.engine,
            "strategy": self.report.strategy,
            "invested_capital": str(self.report.invested_capital),
            "profit_loss": str(self.report.profit_loss),
            "current_capital": str(self.report.current_capital),
            "currency": self.report.currency,
            "result_state": self.report.result_state,
            "created_at": self.report.created_at.isoformat(),
            "completed_at": self.report.completed_at.isoformat(),
            "transaction_id": self.report.transaction_id or "",
        }
        rows = tuple((field, str(values[field])) for field in _CSV_FIELDS)
        amount_rows = tuple(
            (f"assigned_amount.{index + 1}.{amount.label}", str(amount.amount))
            for index, amount in enumerate(self.report.assigned_amounts)
        )
        return rows + amount_rows

    def pdf_document(self) -> bytes:
        """Create a compact, dependency-free PDF from the typed business projection."""

        lines = [
            "Q-Bet Result Report",
            "Simulation result" if self.report.mode == "simulation" else "Execution result",
            "",
            f"Match: {self.report.match}",
            f"Providers: {self.report.provider} / {self.report.counterparty_provider}",
            f"Engine: {self.report.engine}",
            f"Strategy: {self.report.strategy}",
            "",
            f"Invested capital: {_money(self.report.invested_capital, self.report.currency)}",
            *(
                f"Assigned amount - {amount.label}: {_money(amount.amount, self.report.currency)}"
                for amount in self.report.assigned_amounts
            ),
            f"Result: {self.report.result_state}",
            f"Profit/loss: {_money(self.report.profit_loss, self.report.currency)}",
            f"Current capital: {_money(self.report.current_capital, self.report.currency)}",
            "",
            f"Created: {self.report.created_at.isoformat()}",
            f"Completed: {self.report.completed_at.isoformat()}",
        ]
        if self.report.transaction_id:
            lines.append(f"Transaction ID: {self.report.transaction_id}")
        return _pdf_from_lines(lines)


def _money(value: Decimal, currency: str) -> str:
    return f"{value} {currency}"


def _pdf_from_lines(lines: list[str]) -> bytes:
    """Render plain report lines into a minimal PDF with one Helvetica text page."""

    commands = ["BT", "/F1 12 Tf", "72 770 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({_pdf_text(line)}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", "replace")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def _pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def json_bytes(export: CustomerReportExport) -> bytes:
    """Serialize an export deterministically without inspecting HTML."""

    return json.dumps(export.json_document(), indent=2, sort_keys=True).encode("utf-8")
