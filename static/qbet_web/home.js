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

  function scheduleMainCycle() {
    schedulePulse("data", 0);
    schedulePulse("prepare", 1800);
    schedulePulse("evaluate", 3590);
    schedulePulse("protect", 5220);
  }

  function wireMotionCycle(motionId, stationName, arrivalDelayMs) {
    const motion = flow.querySelector(`#${motionId}`);
    if (!motion) {
      return;
    }

    const scheduleArrival = () => schedulePulse(stationName, arrivalDelayMs);
    motion.addEventListener("beginEvent", scheduleArrival);
    motion.addEventListener("repeatEvent", scheduleArrival);
  }

  const mainMotion = flow.querySelector("#flow-motion-main");
  if (mainMotion) {
    mainMotion.addEventListener("repeatEvent", scheduleMainCycle);
  }

  scheduleMainCycle();
  wireMotionCycle("flow-motion-simulation", "simulation", 2380);
  wireMotionCycle("flow-motion-execution", "execution", 2680);
})();
