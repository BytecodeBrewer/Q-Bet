from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_bonus_offer_menu_and_dialog_execute_production_javascript() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the production-JS interaction check")

    result = subprocess.run(
        [
            node,
            str(ROOT / "tests" / "js" / "bonus_offers_interaction.mjs"),
            str(ROOT / "static" / "qbet_web" / "bonus_offers.js"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_bonus_offer_styles_keep_narrow_topbar_and_dialog_responsive() -> None:
    css = (ROOT / "static" / "qbet_web" / "app.css").read_text(encoding="utf-8")

    assert "@media (max-width: 700px)" in css
    assert ".topbar-add { flex: 1; }" in css
    assert ".bonus-offer-fields { grid-template-columns: 1fr; }" in css
