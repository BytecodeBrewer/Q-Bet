(() => {
  const flow = document.querySelector("[data-home-flow]");
  if (!flow) {
    return;
  }

  const svg = flow.querySelector("svg");
  if (!svg) {
    return;
  }

  const FUTURE_REVEAL_DELAY_MS = Number(flow.dataset.futureDelay || 3000);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const desktopFlow = window.matchMedia("(min-width: 781px)");
  const motionToggle = flow.querySelector("[data-flow-motion-toggle]");
  const motionLabel = flow.querySelector("[data-flow-motion-label]");
  const pulseTimers = new Map();
  const pulseFrames = new Map();
  const stations = Array.from(flow.querySelectorAll("[data-flow-station]"));
  const packetSpecs = Array.from(flow.querySelectorAll("[data-flow-packet]"))
    .map((packet) => {
      const pathId = packet.dataset.flowPath;
      const path = pathId ? flow.querySelector(`#${pathId}`) : null;
      const duration = Number(packet.dataset.flowDuration || 0);
      const phase = Number(packet.dataset.flowPhase || 0);
      if (!path || !duration || typeof path.getTotalLength !== "function") {
        return null;
      }

      const checkpoints = (packet.dataset.flowPulse || "")
        .split(",")
        .filter(Boolean)
        .map((entry) => {
          const [name, rawFraction] = entry.split(":");
          return { name, fraction: Number(rawFraction) };
        })
        .filter(({ name, fraction }) => name && Number.isFinite(fraction));

      return {
        packet,
        path,
        duration,
        phase,
        length: path.getTotalLength(),
        checkpoints,
        previousProgress: null,
      };
    })
    .filter(Boolean);

  function pulseStation(name) {
    const station = flow.querySelector(`[data-flow-station="${name}"]`);
    if (!station || motionPaused || reducedMotion.matches) {
      return;
    }

    const existingFrame = pulseFrames.get(station);
    if (existingFrame) {
      window.cancelAnimationFrame(existingFrame);
      pulseFrames.delete(station);
    }

    const existingTimer = pulseTimers.get(station);
    if (existingTimer) {
      window.clearTimeout(existingTimer);
      pulseTimers.delete(station);
    }

    station.classList.remove("is-packet-hit");
    const frame = window.requestAnimationFrame(() => {
      pulseFrames.delete(station);
      if (motionPaused || reducedMotion.matches) {
        return;
      }
      station.classList.add("is-packet-hit");
      const timer = window.setTimeout(() => {
        station.classList.remove("is-packet-hit");
        pulseTimers.delete(station);
      }, 460);
      pulseTimers.set(station, timer);
    });
    pulseFrames.set(station, frame);
  }

  function clearPulseEffects() {
    stations.forEach((station) => {
      const frame = pulseFrames.get(station);
      if (frame) {
        window.cancelAnimationFrame(frame);
        pulseFrames.delete(station);
      }

      const timer = pulseTimers.get(station);
      if (timer) {
        window.clearTimeout(timer);
        pulseTimers.delete(station);
      }

      station.classList.remove("is-packet-hit");
    });
  }

  function pulseCrossedCheckpoints(spec, progress) {
    if (!spec.checkpoints.length) {
      spec.previousProgress = progress;
      return;
    }

    const previous = spec.previousProgress;
    if (previous === null) {
      spec.previousProgress = progress;
      return;
    }

    const crossed = progress >= previous
      ? ({ fraction }) => fraction > previous && fraction <= progress
      : ({ fraction }) => fraction > previous || fraction <= progress;

    spec.checkpoints
      .filter(crossed)
      .forEach(({ name }) => pulseStation(name));
    spec.previousProgress = progress;
  }

  function renderPackets(elapsedMs) {
    packetSpecs.forEach((spec) => {
      const progress = ((elapsedMs + spec.phase) % spec.duration) / spec.duration;
      const point = spec.path.getPointAtLength(spec.length * progress);
      spec.packet.setAttribute("cx", point.x.toFixed(2));
      spec.packet.setAttribute("cy", point.y.toFixed(2));
      pulseCrossedCheckpoints(spec, progress);
    });
  }

  let frameId = null;
  let elapsedMs = 0;
  let lastTimestamp = null;
  let isVisible = true;
  let futureTimer = null;
  let motionPaused = false;

  function tick(timestamp) {
    if (lastTimestamp !== null) {
      elapsedMs += timestamp - lastTimestamp;
    }
    lastTimestamp = timestamp;
    renderPackets(elapsedMs);
    frameId = window.requestAnimationFrame(tick);
  }

  function startAnimation() {
    if (
      frameId !== null
      || motionPaused
      || reducedMotion.matches
      || !desktopFlow.matches
      || !isVisible
    ) {
      return;
    }
    lastTimestamp = null;
    frameId = window.requestAnimationFrame(tick);
  }

  function stopAnimation() {
    if (frameId !== null) {
      window.cancelAnimationFrame(frameId);
      frameId = null;
    }
    lastTimestamp = null;
  }

  function updateMotionControl() {
    if (!motionToggle || !motionLabel) {
      return;
    }
    motionToggle.setAttribute("aria-pressed", String(motionPaused));
    motionLabel.textContent = motionPaused ? "Resume motion" : "Pause motion";
  }

  function cancelFutureReveal() {
    if (futureTimer !== null) {
      window.clearTimeout(futureTimer);
      futureTimer = null;
    }
  }

  function setMotionPaused(paused) {
    motionPaused = paused;
    flow.classList.toggle("is-motion-paused", motionPaused);
    if (motionPaused) {
      stopAnimation();
      cancelFutureReveal();
      clearPulseEffects();
    } else {
      resetPacketState();
      startAnimation();
      if (!flow.classList.contains("is-future-visible")) {
        scheduleFutureReveal();
      }
    }
    updateMotionControl();
  }

  function scheduleFutureReveal() {
    cancelFutureReveal();

    if (reducedMotion.matches) {
      flow.classList.remove("has-motion");
      flow.classList.add("is-future-visible");
      return;
    }

    flow.classList.add("has-motion");
    if (motionPaused || flow.classList.contains("is-future-visible")) {
      return;
    }

    futureTimer = window.setTimeout(() => {
      futureTimer = null;
      if (!motionPaused) {
        flow.classList.add("is-future-visible");
      }
    }, FUTURE_REVEAL_DELAY_MS);
  }

  function resetPacketState() {
    packetSpecs.forEach((spec) => {
      spec.previousProgress = null;
    });
    renderPackets(elapsedMs);
  }

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      ([entry]) => {
        isVisible = Boolean(entry?.isIntersecting);
        if (isVisible) {
          startAnimation();
        } else {
          stopAnimation();
        }
      },
      { rootMargin: "80px" },
    );
    observer.observe(flow);
  }

  motionToggle?.addEventListener("click", () => {
    setMotionPaused(!motionPaused);
  });

  updateMotionControl();

  reducedMotion.addEventListener("change", () => {
    scheduleFutureReveal();
    if (reducedMotion.matches) {
      stopAnimation();
      clearPulseEffects();
      return;
    }
    resetPacketState();
    startAnimation();
  });

  desktopFlow.addEventListener("change", () => {
    if (!desktopFlow.matches) {
      stopAnimation();
      return;
    }
    resetPacketState();
    startAnimation();
  });

  scheduleFutureReveal();
  resetPacketState();
  startAnimation();
})();
