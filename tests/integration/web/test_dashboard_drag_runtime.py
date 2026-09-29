import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DND_JS = PROJECT_ROOT / "static" / "qbet_web" / "dashboard_dnd.js"
DASHBOARD_JS = PROJECT_ROOT / "static" / "qbet_web" / "dashboard.js"


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


def test_drag_geometry_uses_card_edges_midpoints_and_grid_rows() -> None:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
global.globalThis = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });
const dnd = global.QBetDashboardDnd;
const rect = (left, top, width, height) => ({ left, top, right: left + width, bottom: top + height, width, height });
const sameRow = rect(0, 0, 100, 80);
const peer = { id: "peer", card: "peer", rect: rect(120, 0, 100, 80) };
const nextRow = { id: "next-row", card: "next-row", rect: rect(0, 110, 100, 80) };
const placement = (point, slot, candidate, axis = "free") => {
  const result = dnd.closestInsertionSlot(point, slot, [candidate], 10);
  return result && (axis !== "y" || result.axis === "y") ? result : null;
};

class Grid {
  constructor(children) { this.children = children; children.forEach((child) => { child.parentElement = this; }); }
  querySelectorAll() { return this.children.filter((child) => child.dataset?.widgetId); }
  insertBefore(node, reference) {
    this.children = this.children.filter((child) => child !== node);
    const index = reference ? this.children.indexOf(reference) : -1;
    this.children.splice(index < 0 ? this.children.length : index, 0, node);
    node.parentElement = this;
  }
  appendChild(node) { this.insertBefore(node, null); }
}
const widget = (id) => ({ dataset: { widgetId: id } });
const active = widget("active"), a = widget("a"), b = widget("b"), c = widget("c");
const placeholder = { slot: true };
const grid = new Grid([active, placeholder, a, b, c]);
const movedForward = dnd.movePlaceholder(grid, placeholder, b, "after", active);
const forwardOrder = grid.children.filter((node) => node !== active).map((node) => node.slot ? "slot" : node.dataset.widgetId);
const unchangedSlot = dnd.movePlaceholder(grid, placeholder, b, "after", active);
const movedBackward = dnd.movePlaceholder(grid, placeholder, a, "before", active);
const backwardOrder = grid.children.filter((node) => node !== active).map((node) => node.slot ? "slot" : node.dataset.widgetId);
dnd.restoreOrder(grid, ["active", "a", "b", "c"]);
placeholder.parentElement = null;
grid.children = grid.children.filter((node) => node !== placeholder);
process.stdout.write(JSON.stringify({
  forward: placement({ x: 190, y: 30 }, rect(0, 0, 100, 80), peer),
  backward: placement({ x: 130, y: 30 }, rect(220, 0, 100, 80), peer),
  hysteresis: placement({ x: 165, y: 30 }, rect(0, 0, 100, 80), peer),
  multiColumnRow: placement({ x: 30, y: 60 }, rect(120, 110, 100, 80), { id: "upper-row", card: "next-row", rect: rect(0, 0, 100, 80) }),
  narrowSingleColumn: placement({ x: 30, y: 170 }, rect(0, 0, 100, 80), nextRow, "y"),
  movedForward, forwardOrder, unchangedSlot, movedBackward, backwardOrder,
  cancelRestores: dnd.order(grid),
  outsideDropPersists: dnd.shouldPersist({ dragStarted: true, commit: false, before: ["a", "b"], after: ["b", "a"] }),
}));
"""
    completed = subprocess.run(
        ["node", "-e", harness, str(DND_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["forward"] == {"card": "peer", "position": "after", "axis": "x"}
    assert result["backward"] == {"card": "peer", "position": "before", "axis": "x"}
    assert result["hysteresis"] is None
    assert result["multiColumnRow"] == {
        "card": "next-row",
        "position": "after",
        "axis": "y",
    }
    assert result["narrowSingleColumn"] == {
        "card": "next-row",
        "position": "after",
        "axis": "y",
    }
    assert result["movedForward"] is True
    assert result["forwardOrder"] == ["a", "b", "slot", "c"]
    assert result["unchangedSlot"] is False
    assert result["movedBackward"] is True
    assert result["backwardOrder"] == ["slot", "a", "b", "c"]
    assert result["cancelRestores"] == ["active", "a", "b", "c"]
    assert result["outsideDropPersists"] is False



def test_escape_cancels_pointer_drag_independent_of_previous_focus() -> None:
    harness = r"""
const fs = require("fs");
const vm = require("vm");

class ClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  contains(value) { return this.values.has(value); }
}

const makeStyle = () => ({
  setProperty(name, value) { this[name] = value; },
  removeProperty(name) { delete this[name]; },
});
const rect = (left, top, width, height) => ({
  left,
  top,
  right: left + width,
  bottom: top + height,
  width,
  height,
});

class Widget {
  constructor(id, bounds) {
    this.dataset = { widgetId: id };
    this.bounds = bounds;
    this.offsetTop = bounds.top;
    this.offsetHeight = bounds.height;
    this.classList = new ClassList();
    this.style = makeStyle();
    this.parentElement = null;
  }
  getBoundingClientRect() { return this.bounds; }
  getAttribute(name) { return name === "style" ? null : null; }
  removeAttribute(name) {
    if (name === "style") this.style = makeStyle();
  }
  setAttribute() {}
  animate() {}
}

class Placeholder {
  constructor() {
    this.className = "";
    this.style = {};
    this.parentElement = null;
    this.bounds = rect(0, 0, 100, 80);
  }
  setAttribute() {}
  getBoundingClientRect() { return this.bounds; }
  remove() {
    if (!this.parentElement) return;
    this.parentElement.children = this.parentElement.children
      .filter((child) => child !== this);
    this.parentElement = null;
  }
}

class Handle {
  constructor(card) {
    this.card = card;
    this.listeners = new Map();
    this.captured = new Set();
  }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  dispatch(type, event) {
    for (const callback of this.listeners.get(type) || []) callback(event);
  }
  closest() { return this.card; }
  setPointerCapture(pointerId) { this.captured.add(pointerId); }
  hasPointerCapture(pointerId) { return this.captured.has(pointerId); }
  releasePointerCapture(pointerId) {
    this.captured.delete(pointerId);
    this.dispatch("lostpointercapture", { pointerId });
  }
}

class Grid {
  constructor(children, handles) {
    this.children = children;
    this.handles = handles;
    this.classList = new ClassList();
    this.dataset = { plane: "simulation" };
    children.forEach((child) => { child.parentElement = this; });
  }
  querySelectorAll(selector) {
    if (selector === "[data-widget-id]") {
      return this.children.filter((child) => child.dataset?.widgetId);
    }
    if (selector === "[data-drag-handle]") return this.handles;
    return [];
  }
  insertBefore(node, reference) {
    this.children = this.children.filter((child) => child !== node);
    const index = reference ? this.children.indexOf(reference) : -1;
    this.children.splice(index < 0 ? this.children.length : index, 0, node);
    node.parentElement = this;
  }
  appendChild(node) { this.insertBefore(node, null); }
  getBoundingClientRect() { return rect(-20, -20, 400, 240); }
}

class Body {
  constructor() { this.children = []; }
  appendChild(node) {
    if (node.parentElement?.children) {
      node.parentElement.children = node.parentElement.children
        .filter((child) => child !== node);
    }
    this.children.push(node);
    node.parentElement = this;
  }
}

const cardA = new Widget("a", rect(0, 0, 100, 80));
const cardB = new Widget("b", rect(120, 0, 100, 80));
const handle = new Handle(cardA);
const grid = new Grid([cardA, cardB], [handle]);
const unrelatedControl = { id: "unrelated-control" };
const documentListeners = new Map();

const stateForm = {
  dataset: { layoutUrl: "/layout/" },
  querySelector() { return { value: "csrf-token" }; },
};

global.globalThis = global;
global.document = {
  activeElement: unrelatedControl,
  body: new Body(),
  getElementById(id) { return id === "dashboard-layout-state" ? stateForm : null; },
  querySelectorAll(selector) { return selector === "[data-widget-grid]" ? [grid] : []; },
  createElement() { return new Placeholder(); },
  addEventListener(type, callback) {
    if (!documentListeners.has(type)) documentListeners.set(type, []);
    documentListeners.get(type).push(callback);
  },
};
global.window = {
  matchMedia() { return { matches: false }; },
  addEventListener() {},
};
let fetchCalls = 0;
global.fetch = async () => {
  fetchCalls += 1;
  return { ok: true, status: 200 };
};

vm.runInThisContext(
  fs.readFileSync(process.argv[1], "utf8"),
  { filename: process.argv[1] },
);
vm.runInThisContext(
  fs.readFileSync(process.argv[2], "utf8"),
  { filename: process.argv[2] },
);

const preventable = (extra) => ({
  ...extra,
  defaultPrevented: false,
  preventDefault() { this.defaultPrevented = true; },
});

handle.dispatch("pointerdown", preventable({
  pointerType: "mouse",
  button: 0,
  pointerId: 7,
  clientX: 10,
  clientY: 10,
}));
handle.dispatch("pointermove", preventable({
  pointerType: "mouse",
  pointerId: 7,
  clientX: 30,
  clientY: 30,
}));

const floatingBeforeEscape =
  cardA.classList.contains("is-dragging") &&
  grid.classList.contains("is-reordering") &&
  cardA.parentElement === global.document.body &&
  grid.children.some((child) => child.className === "dashboard-drag-placeholder");

const firstEscape = preventable({ key: "Escape" });
for (const callback of documentListeners.get("keydown") || []) callback(firstEscape);

const secondEscape = preventable({ key: "Escape" });
for (const callback of documentListeners.get("keydown") || []) callback(secondEscape);

const dnd = global.QBetDashboardDnd;
process.stdout.write(JSON.stringify({
  focusStayedUnrelated: global.document.activeElement === unrelatedControl,
  floatingBeforeEscape,
  firstEscapePrevented: firstEscape.defaultPrevented,
  draggingAfterEscape: cardA.classList.contains("is-dragging"),
  cardReturnedToGrid: cardA.parentElement === grid,
  reorderingAfterEscape: grid.classList.contains("is-reordering"),
  placeholderCount: grid.children.filter(
    (child) => child.className === "dashboard-drag-placeholder"
  ).length,
  pointerCaptureReleased: !handle.hasPointerCapture(7),
  restoredOrder: dnd.order(grid),
  fetchCalls,
  secondEscapePrevented: secondEscape.defaultPrevented,
}));
"""
    completed = subprocess.run(
        ["node", "-e", harness, str(DND_JS), str(DASHBOARD_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result == {
        "focusStayedUnrelated": True,
        "floatingBeforeEscape": True,
        "firstEscapePrevented": True,
        "draggingAfterEscape": False,
        "cardReturnedToGrid": True,
        "reorderingAfterEscape": False,
        "placeholderCount": 0,
        "pointerCaptureReleased": True,
        "restoredOrder": ["a", "b"],
        "fetchCalls": 0,
        "secondEscapePrevented": False,
    }


def test_dashboard_script_routes_abort_paths_through_cancel_semantics() -> None:
    script = (PROJECT_ROOT / "static" / "qbet_web" / "dashboard.js").read_text(
        encoding="utf-8"
    )

    assert 'handle.addEventListener("pointercancel"' in script
    assert 'handle.addEventListener("lostpointercapture"' in script
    assert 'document.addEventListener("keydown"' in script
    assert 'event.key === "Escape" && cancelActivePointerDrag' in script
    assert "cancelActivePointerDrag = null;" in script
    assert 'dnd.restoreOrder(grid, before);' in script
    assert 'placeholder?.remove();' in script
    assert 'grid.insertBefore(card, placeholder);' in script
    assert "document.body.appendChild(card);" in script
    assert 'position: fixed' not in script
    assert "dnd.pointInside(grid.getBoundingClientRect()" in script


def test_dashboard_drag_styles_float_the_card_and_disable_placeholder_motion() -> None:
    stylesheet = (PROJECT_ROOT / "static" / "qbet_web" / "phase2_visual.css").read_text(
        encoding="utf-8"
    )
    assert ".draggable-card.is-dragging {" in stylesheet
    assert "position: fixed !important;" in stylesheet
    assert ".dashboard-drag-placeholder {" in stylesheet
    reduced_motion = stylesheet.split("@media (prefers-reduced-motion: reduce)")[-1]
    assert ".dashboard-drag-placeholder" in reduced_motion
