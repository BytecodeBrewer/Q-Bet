import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HOME_JS = PROJECT_ROOT / "static" / "qbet_web" / "home.js"


def test_home_flow_pause_control_stops_and_resumes_animation() -> None:
    harness = r"""
const fs = require("fs");
const vm = require("vm");

const rafCallbacks = new Map();
const timeoutCallbacks = new Map();
let nextRafId = 1;
let nextTimeoutId = 1;
let cancelled = 0;

const buttonAttributes = {};
let clickHandler = null;
const button = {
  addEventListener(type, handler) {
    if (type === "click") clickHandler = handler;
  },
  setAttribute(name, value) {
    buttonAttributes[name] = value;
  },
};
const label = { textContent: "" };
const classes = new Set();
const classList = {
  add(name) { classes.add(name); },
  remove(name) { classes.delete(name); },
  toggle(name, enabled) {
    if (enabled) classes.add(name);
    else classes.delete(name);
  },
  contains(name) { return classes.has(name); },
};

const path = {
  getTotalLength() { return 100; },
  getPointAtLength(value) { return { x: value, y: 10 }; },
};
const packet = {
  dataset: {
    flowPath: "flow-path",
    flowDuration: "7000",
    flowPhase: "0",
    flowPulse: "",
  },
  setAttribute() {},
};

const flow = {
  dataset: { futureDelay: "3000" },
  classList,
  querySelector(selector) {
    if (selector === "svg") return {};
    if (selector === "#flow-path") return path;
    if (selector === "[data-flow-motion-toggle]") return button;
    if (selector === "[data-flow-motion-label]") return label;
    return null;
  },
  querySelectorAll(selector) {
    return selector === "[data-flow-packet]" ? [packet] : [];
  },
};

global.document = {
  querySelector(selector) {
    return selector === "[data-home-flow]" ? flow : null;
  },
};

global.window = {
  matchMedia(query) {
    return {
      matches: query.includes("prefers-reduced-motion") ? false : true,
      addEventListener() {},
    };
  },
  requestAnimationFrame(callback) {
    const id = nextRafId++;
    rafCallbacks.set(id, callback);
    return id;
  },
  cancelAnimationFrame(id) {
    if (rafCallbacks.delete(id)) cancelled += 1;
  },
  setTimeout(callback) {
    const id = nextTimeoutId++;
    timeoutCallbacks.set(id, callback);
    return id;
  },
  clearTimeout(id) {
    timeoutCallbacks.delete(id);
  },
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), {
  filename: process.argv[1],
});

const initial = {
  label: label.textContent,
  pressed: buttonAttributes["aria-pressed"],
  pendingFrames: rafCallbacks.size,
  pendingFutureTimers: timeoutCallbacks.size,
};

clickHandler();
const paused = {
  label: label.textContent,
  pressed: buttonAttributes["aria-pressed"],
  pendingFrames: rafCallbacks.size,
  pausedClass: classes.has("is-motion-paused"),
  pendingFutureTimers: timeoutCallbacks.size,
  futureVisible: classes.has("is-future-visible"),
  cancelled,
};

clickHandler();
const resumed = {
  label: label.textContent,
  pressed: buttonAttributes["aria-pressed"],
  pendingFrames: rafCallbacks.size,
  pausedClass: classes.has("is-motion-paused"),
  pendingFutureTimers: timeoutCallbacks.size,
  futureVisible: classes.has("is-future-visible"),
};

process.stdout.write(JSON.stringify({ initial, paused, resumed }));
"""

    completed = subprocess.run(
        ["node", "-e", harness, str(HOME_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result == {
        "initial": {
            "label": "Pause motion",
            "pressed": "false",
            "pendingFrames": 1,
            "pendingFutureTimers": 1,
        },
        "paused": {
            "label": "Resume motion",
            "pressed": "true",
            "pendingFrames": 0,
            "pausedClass": True,
            "pendingFutureTimers": 0,
            "futureVisible": False,
            "cancelled": 1,
        },
        "resumed": {
            "label": "Pause motion",
            "pressed": "false",
            "pendingFrames": 1,
            "pausedClass": False,
            "pendingFutureTimers": 1,
            "futureVisible": False,
        },
    }
