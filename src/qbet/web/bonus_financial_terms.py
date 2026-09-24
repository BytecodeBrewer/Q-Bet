"""Provider-specific financial terms required by connected BonusEngine Simulation."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Protocol

from django.conf import settings
from pydantic import Field

from qbet.calculations import SportsbookTaxMode
from qbet.domain.models import DomainModel, Identifier


class SportsbookFinancialProfile(DomainModel):
    """Explicit fee/tax treatment for one canonical sportsbook provider."""

    provider_id: Identifier
    fee_rate: Decimal = Field(
        ge=Decimal(0),
        lt=Decimal(1),
        allow_inf_nan=False,
    )
    tax_mode: SportsbookTaxMode


class SportsbookFinancialProfileRepository(Protocol):
    def get(self, provider_id: Identifier) -> SportsbookFinancialProfile | None: ...


class SettingsSportsbookFinancialProfileRepository:
    """Load provider financial terms from explicit application configuration."""

    def get(self, provider_id: Identifier) -> SportsbookFinancialProfile | None:
        raw = getattr(settings, "QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS", "")
        if not raw:
            return None
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as error:
            raise ValueError("bonus sportsbook financial terms are invalid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("bonus sportsbook financial terms must be a provider mapping")
        item = payload.get(str(provider_id))
        if item is None:
            return None
        if not isinstance(item, dict):
            raise ValueError("bonus sportsbook financial profile must be an object")
        return SportsbookFinancialProfile.model_validate(
            {
                **item,
                "provider_id": str(provider_id),
            }
        )
