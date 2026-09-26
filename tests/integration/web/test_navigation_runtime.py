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
const elementListeners = new Map();
const documentListeners = {};
const classes = new Set(["sidebar-collapsed"]);
const storage = new Map();

if (initialStorage !== "__EMPTY__") {
  storage.set("qbet.sidebar.collapsed", initialStorage);
}

const listen = (element, name, callback) => {
  if (!elementListeners.has(element)) elementListeners.set(element, {});
  elementListeners.get(element)[name] = callback;
};

const attributes = new Map();
const makeElement = (name) => ({
  name,
  hidden: false,
  focused: false,
  setAttribute(key, value) {
    if (!attributes.has(name)) attributes.set(name, {});
    attributes.get(name)[key] = String(value);
  },
  addEventListener(eventName, callback) {
    listen(this, eventName, callback);
  },
  focus() {
    this.focused = true;
  },
  contains(target) {
    return target === this || target?.owner === this;
  },
});

const toggle = makeElement("toggle");
const closeButton = makeElement("close");
const backdrop = makeElement("backdrop");
const sidebar = makeElement("sidebar");
sidebar.querySelector = (selector) =>
  selector === "[data-sidebar-close]" ? closeButton : null;

const accountRoot = makeElement("accountRoot");
const accountToggle = makeElement("accountToggle");
const accountPanel = makeElement("accountPanel");
const accountItem = makeElement("accountItem");
accountPanel.hidden = true;
accountPanel.querySelector = (selector) =>
  selector === '[role="menuitem"]' ? accountItem : null;
accountRoot.querySelector = (selector) => {
  if (selector === "[data-account-menu-toggle]") return accountToggle;
  if (selector === "[data-account-menu-panel]") return accountPanel;
  return null;
};
accountToggle.owner = accountRoot;
accountPanel.owner = accountRoot;
accountItem.owner = accountRoot;

global.HTMLElement = function HTMLElement() {};
Object.setPrototypeOf(accountItem, HTMLElement.prototype);

global.document = {
  querySelector(selector) {
    if (selector === "[data-sidebar-toggle]") return toggle;
    if (selector === "[data-sidebar-backdrop]") return backdrop;
    if (selector === "[data-account-menu]") return accountRoot;
    return null;
  },
  getElementById(id) {
    return id === "app-sidebar" ? sidebar : null;
  },
  addEventListener(name, callback) {
    documentListeners[name] ??= [];
    documentListeners[name].push(callback);
  },
  body: {
    classList: {
      toggle(name, enabled) {
        if (enabled) classes.add(name);
        else classes.delete(name);
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

const fire = (element, name, event = {}) => {
  const callback = elementListeners.get(element)?.[name];
  if (callback) callback(event);
};
const fireDocument = (name, event = {}) => {
  for (const callback of documentListeners[name] ?? []) callback(event);
};

vm.runInThisContext(fs.readFileSync(scriptPath, "utf8"), { filename: scriptPath });

const attr = (element, key) => attributes.get(element.name)?.[key] ?? null;
const snapshot = () => ({
  collapsed: classes.has("sidebar-collapsed"),
  expanded: attr(toggle, "aria-expanded"),
  sidebarHidden: attr(sidebar, "aria-hidden"),
  stored: storage.has("qbet.sidebar.collapsed")
    ? storage.get("qbet.sidebar.collapsed")
    : null,
  accountExpanded: attr(accountToggle, "aria-expanded"),
  accountHidden: accountPanel.hidden,
});

const initial = snapshot();
fire(toggle, "click");
const afterOpen = snapshot();
fire(toggle, "click");
const afterClose = snapshot();

fire(toggle, "click");
fire(closeButton, "click");
const afterCloseButton = snapshot();

fire(toggle, "click");
fireDocument("keydown", { key: "Escape" });
const afterEscape = snapshot();

fire(accountToggle, "click");
const accountOpen = snapshot();
fireDocument("keydown", { key: "Escape" });
const accountClosedByEscape = snapshot();

const repeated = [];
for (let index = 0; index < 10; index += 1) {
  fire(toggle, "click");
  repeated.push(snapshot());
}

process.stdout.write(JSON.stringify({
  initial,
  afterOpen,
  afterClose,
  afterCloseButton,
  afterEscape,
  accountOpen,
  accountClosedByEscape,
  repeated,
  toggleFocused: toggle.focused,
  accountToggleFocused: accountToggle.focused,
}));
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


def test_sidebar_fresh_state_is_collapsed_then_persists_explicit_choice() -> None:
    states = _run_navigation_script()

    assert states["initial"] == {
        "collapsed": True,
        "expanded": "false",
        "sidebarHidden": "true",
        "stored": None,
        "accountExpanded": None,
        "accountHidden": True,
    }
    assert states["afterOpen"]["collapsed"] is False
    assert states["afterOpen"]["expanded"] == "true"
    assert states["afterOpen"]["stored"] == "0"
    assert states["afterClose"]["collapsed"] is True
    assert states["afterClose"]["expanded"] == "false"
    assert states["afterClose"]["stored"] == "1"


def test_sidebar_restores_persisted_explicit_open_state() -> None:
    states = _run_navigation_script("0")

    assert states["initial"]["collapsed"] is False
    assert states["initial"]["expanded"] == "true"
    assert states["initial"]["sidebarHidden"] == "false"
    assert states["initial"]["stored"] == "0"


def test_sidebar_close_and_escape_restore_collapsed_state_and_focus() -> None:
    states = _run_navigation_script()

    assert states["afterCloseButton"]["collapsed"] is True
    assert states["afterCloseButton"]["stored"] == "1"
    assert states["afterEscape"]["collapsed"] is True
    assert states["toggleFocused"] is True


def test_account_menu_opens_and_escape_closes_with_focus_return() -> None:
    states = _run_navigation_script()

    assert states["accountOpen"]["accountExpanded"] == "true"
    assert states["accountOpen"]["accountHidden"] is False
    assert states["accountClosedByEscape"]["accountExpanded"] == "false"
    assert states["accountClosedByEscape"]["accountHidden"] is True
    assert states["accountToggleFocused"] is True


def test_shell_css_keeps_main_width_independent_from_right_sidebar() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    shell = css.split("/* Application shell */", maxsplit=1)[1].split(
        "/* Activity, preference and export feedback */", maxsplit=1
    )[0]

    assert ".app-shell {" in shell
    assert "display: block;" in shell
    assert "grid-template-columns: 250px minmax(0, 1fr)" not in shell
    assert ".app-main {" in shell
    assert "max-width: none;" in shell
    assert ".app-sidebar {" in shell
    assert "position: fixed;" in shell
    assert "right: 12px;" in shell
    assert "transform: translateX(calc(100% + 28px));" in shell
    assert "display: none;" not in shell.split("body.sidebar-collapsed .app-sidebar", 1)[1].split("}", 1)[0]


def test_narrow_viewport_uses_right_overlay_instead_of_content_reflow() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    shell = css.split("/* Application shell */", maxsplit=1)[1].split(
        "/* Activity, preference and export feedback */", maxsplit=1
    )[0]
    mobile = shell.split("@media (max-width: 700px)", maxsplit=1)[1]

    assert ".app-shell { display: block; }" in mobile
    assert "right: 12px;" in mobile
    assert "width: min(360px, calc(100vw - 24px));" in mobile
    assert ".app-sidebar-backdrop {" in mobile
    assert ".app-main { padding: 22px 16px 40px; }" in mobile
    assert "position: static" not in mobile
    assert "width: 100%; height: auto" not in mobile


def test_reduced_motion_contract_also_covers_navigation_transition() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition-duration: .001ms !important;" in css


def test_repeated_sidebar_toggles_return_to_stable_geometry_state_contract() -> None:
    states = _run_navigation_script()
    repeated = states["repeated"]

    assert len(repeated) == 10
    for index, state in enumerate(repeated):
        expected_collapsed = index % 2 == 1
        assert state["collapsed"] is expected_collapsed
        assert state["expanded"] == ("false" if expected_collapsed else "true")
    assert repeated[-1]["collapsed"] is True
    assert repeated[-1]["stored"] == "1"
