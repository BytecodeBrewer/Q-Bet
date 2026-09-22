import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
NAVIGATION_JS = PROJECT_ROOT / "static" / "qbet_web" / "navigation.js"
APP_CSS = PROJECT_ROOT / "static" / "qbet_web" / "app.css"


def _run_navigation_script(initial_storage: str | None = None) -> dict[str, object]:
    harness = r"""
const fs = require("fs");
const vm = require("vm");

const scriptPath = process.argv[1];
const initialStorage = process.argv[2];
const listeners = {};
const classes = new Set();
const attributes = {};
const storage = new Map();

if (initialStorage !== "__EMPTY__") {
  storage.set("qbet.sidebar.collapsed", initialStorage);
}

const toggle = {
  setAttribute(name, value) {
    attributes[name] = String(value);
  },
  addEventListener(name, callback) {
    listeners[name] = callback;
  },
};
const sidebar = {};

global.document = {
  querySelector(selector) {
    return selector === "[data-sidebar-toggle]" ? toggle : null;
  },
  getElementById(id) {
    return id === "app-sidebar" ? sidebar : null;
  },
  body: {
    classList: {
      toggle(name, enabled) {
        if (enabled) {
          classes.add(name);
        } else {
          classes.delete(name);
        }
      },
      contains(name) {
        return classes.has(name);
      },
    },
  },
};

global.window = {
  localStorage: {
    getItem(key) {
      return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
      storage.set(key, String(value));
    },
  },
};

vm.runInThisContext(fs.readFileSync(scriptPath, "utf8"), { filename: scriptPath });

const snapshot = () => ({
  collapsed: classes.has("sidebar-collapsed"),
  expanded: attributes["aria-expanded"],
  stored: storage.has("qbet.sidebar.collapsed")
    ? storage.get("qbet.sidebar.collapsed")
    : null,
});

const initial = snapshot();
listeners.click();
const afterFirstClick = snapshot();
listeners.click();
const afterSecondClick = snapshot();

process.stdout.write(
  JSON.stringify({ initial, afterFirstClick, afterSecondClick })
);
"""
    completed = subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(NAVIGATION_JS),
            initial_storage if initial_storage is not None else "__EMPTY__",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_sidebar_toggle_executes_collapse_expand_and_persists_state() -> None:
    states = _run_navigation_script()

    assert states["initial"] == {
        "collapsed": False,
        "expanded": "true",
        "stored": None,
    }
    assert states["afterFirstClick"] == {
        "collapsed": True,
        "expanded": "false",
        "stored": "1",
    }
    assert states["afterSecondClick"] == {
        "collapsed": False,
        "expanded": "true",
        "stored": "0",
    }


def test_sidebar_toggle_restores_persisted_collapsed_state() -> None:
    states = _run_navigation_script("1")

    assert states["initial"] == {
        "collapsed": True,
        "expanded": "false",
        "stored": "1",
    }


def test_narrow_viewport_css_contract_avoids_fixed_sidebar_overlap() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 700px)", maxsplit=1)[1]

    assert ".app-topbar { flex-wrap: wrap; }" in mobile
    assert ".topbar-actions { order: 3; width: 100%; margin-left: 0; }" in mobile
    assert ".app-shell { display: block; }" in mobile
    assert (
        ".app-sidebar { position: static; width: 100%; height: auto; "
        "border-right: 0; border-bottom: 1px solid var(--border); }"
    ) in mobile
    assert "body.sidebar-collapsed .app-sidebar { display: none; }" in mobile
    assert ".app-main { padding: 22px 16px 40px; }" in mobile
    assert ".settings-toc { grid-template-columns: 1fr; }" in mobile
