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
let clearedTimeouts = 0;

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
const stationClasses = new Set();
const stationClassList = {
  add(name) { stationClasses.add(name); },
  remove(name) { stationClasses.delete(name); },
};
const station = { classList: stationClassList };

const packet = {
  dataset: {
    flowPath: "flow-path",
    flowDuration: "7000",
    flowPhase: "0",
    flowPulse: "station:.05",
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
    if (selector === '[data-flow-station="station"]') return station;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-flow-packet]") return [packet];
    if (selector === "[data-flow-station]") return [station];
    return [];
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
    if (timeoutCallbacks.delete(id)) clearedTimeouts += 1;
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

// Advance the packet across the first checkpoint and execute the queued
// station-pulse frame so Pause must stop an already-active pulse as well
// as the main animation frame and delayed Future reveal.
let frameEntry = rafCallbacks.entries().next().value;
rafCallbacks.delete(frameEntry[0]);
frameEntry[1](0);

frameEntry = rafCallbacks.entries().next().value;
rafCallbacks.delete(frameEntry[0]);
frameEntry[1](500);

frameEntry = rafCallbacks.entries().next().value;
rafCallbacks.delete(frameEntry[0]);
frameEntry[1](500);

const beforePause = {
  pendingFrames: rafCallbacks.size,
  pendingTimers: timeoutCallbacks.size,
  stationPulse: stationClasses.has("is-packet-hit"),
};

clickHandler();
const paused = {
  label: label.textContent,
  pressed: buttonAttributes["aria-pressed"],
  pendingFrames: rafCallbacks.size,
  pausedClass: classes.has("is-motion-paused"),
  pendingFutureTimers: timeoutCallbacks.size,
  futureVisible: classes.has("is-future-visible"),
  stationPulse: stationClasses.has("is-packet-hit"),
  cancelled,
  clearedTimeouts,
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

process.stdout.write(JSON.stringify({ initial, beforePause, paused, resumed }));
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
        "beforePause": {
            "pendingFrames": 1,
            "pendingTimers": 2,
            "stationPulse": True,
        },
        "paused": {
            "label": "Resume motion",
            "pressed": "true",
            "pendingFrames": 0,
            "pausedClass": True,
            "pendingFutureTimers": 0,
            "futureVisible": False,
            "stationPulse": False,
            "cancelled": 1,
            "clearedTimeouts": 2,
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
