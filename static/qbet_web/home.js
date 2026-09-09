(() => {
  const flow = document.querySelector("[data-home-flow]");
  if (!flow) {
    return;
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (reducedMotion.matches) {
    return;
  }

  const timers = new Set();

  function pulseStation(name) {
    const station = flow.querySelector(`[data-flow-station="${name}"]`);
    if (!station) {
      return;
    }

    station.classList.remove("is-packet-hit");
    window.requestAnimationFrame(() => {
      station.classList.add("is-packet-hit");
      const timer = window.setTimeout(() => {
        station.classList.remove("is-packet-hit");
        timers.delete(timer);
      }, 520);
      timers.add(timer);
    });
  }

  function schedulePulse(name, delayMs) {
    const timer = window.setTimeout(() => {
      timers.delete(timer);
      pulseStation(name);
    }, delayMs);
    timers.add(timer);
  }

  function scheduleMainStations() {
    pulseStation("data");
    schedulePulse("prepare", 1400);
    schedulePulse("evaluate", 2800);
  }

  const mainMotion = flow.querySelector("#flow-motion-main");
  const simulationMotion = flow.querySelector("#flow-motion-simulation");
  const executionMotion = flow.querySelector("#flow-motion-execution");

  let observedInitialMainBegin = false;
  if (mainMotion) {
    mainMotion.addEventListener("beginEvent", () => {
      observedInitialMainBegin = true;
      scheduleMainStations();
    });
    mainMotion.addEventListener("endEvent", () => pulseStation("protect"));
  }

  simulationMotion?.addEventListener("endEvent", () => pulseStation("simulation"));
  executionMotion?.addEventListener("endEvent", () => pulseStation("execution"));

  window.setTimeout(() => {
    if (!observedInitialMainBegin) {
      scheduleMainStations();
    }
  }, 60);
})();
