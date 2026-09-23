import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DND_JS = PROJECT_ROOT / "static" / "qbet_web" / "dashboard_dnd.js"


def _run_dnd_contract() -> dict[str, object]:
    harness = r"""
const fs = require("fs");
const vm = require("vm");

global.globalThis = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });
const dnd = global.QBetDashboardDnd;
const rect = { left: 10, right: 110, top: 20, bottom: 120 };
const before = ["bonus", "sports_capital"];
const changed = ["sports_capital", "bonus"];

process.stdout.write(JSON.stringify({
  inside: dnd.pointInside(rect, 50, 60),
  outside: dnd.pointInside(rect, 150, 60),
  validDropPersists: dnd.shouldPersist({
    dragStarted: true,
    commit: true,
    before,
    after: changed,
  }),
  unchangedDropDoesNotPersist: dnd.shouldPersist({
    dragStarted: true,
    commit: true,
    before,
    after: before,
  }),
  cancelDoesNotPersist: dnd.shouldPersist({
    dragStarted: true,
    commit: false,
    before,
    after: changed,
  }),
  clickDoesNotPersist: dnd.shouldPersist({
    dragStarted: false,
    commit: true,
    before,
    after: changed,
  }),
}));
"""
    completed = subprocess.run(
        ["node", "-e", harness, str(DND_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_drag_commit_contract_persists_only_valid_changed_drop() -> None:
    result = _run_dnd_contract()

    assert result == {
        "inside": True,
        "outside": False,
        "validDropPersists": True,
        "unchangedDropDoesNotPersist": False,
        "cancelDoesNotPersist": False,
        "clickDoesNotPersist": False,
    }


def test_dashboard_script_routes_abort_paths_through_cancel_semantics() -> None:
    script = (PROJECT_ROOT / "static" / "qbet_web" / "dashboard.js").read_text(
        encoding="utf-8"
    )

    assert 'handle.addEventListener("pointercancel"' in script
    assert 'handle.addEventListener("lostpointercapture"' in script
    assert 'event.key === "Escape"' in script
    assert 'dnd.restoreOrder(grid, before);' in script
    assert "dnd.pointInside(grid.getBoundingClientRect()" in script
