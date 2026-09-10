(() => {
  const flow = document.querySelector("[data-home-flow]");
  if (!flow) {
    return;
  }

  const svg = flow.querySelector("svg");
  if (!svg) {
    return;
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const pulseTimers = new WeakMap();
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
    if (!station) {
      return;
    }

    const existingTimer = pulseTimers.get(station);
    if (existingTimer) {
      window.clearTimeout(existingTimer);
    }
    station.classList.remove("is-packet-hit");
    window.requestAnimationFrame(() => {
      station.classList.add("is-packet-hit");
      const timer = window.setTimeout(() => {
        station.classList.remove("is-packet-hit");
        pulseTimers.delete(station);
      }, 500);
      pulseTimers.set(station, timer);
    });
  }

  function pulseCrossedCheckpoints(spec, progress) {
    if (!spec.checkpoints.length) {
      spec.previousProgress = progress;
      return;
    }

    const previous = spec.previousProgress;
    if (previous === null) {
      spec.checkpoints
        .filter(({ fraction }) => Math.abs(fraction - progress) < 0.012)
        .forEach(({ name }) => pulseStation(name));
      spec.previousProgress = progress;
      return;
    }

    if (progress >= previous) {
      spec.checkpoints
        .filter(({ fraction }) => fraction > previous && fraction <= progress)
        .forEach(({ name }) => pulseStation(name));
    } else {
      spec.checkpoints
        .filter(({ fraction }) => fraction > previous && fraction <= 1)
        .forEach(({ name }) => pulseStation(name));
      spec.checkpoints
        .filter(({ fraction }) => fraction >= 0 && fraction <= progress)
        .forEach(({ name }) => pulseStation(name));
    }

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

  function tick(timestamp) {
    if (lastTimestamp !== null) {
      elapsedMs += timestamp - lastTimestamp;
    }
    lastTimestamp = timestamp;
    renderPackets(elapsedMs);
    frameId = window.requestAnimationFrame(tick);
  }

  function startAnimation() {
    if (frameId !== null || reducedMotion.matches || !isVisible) {
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

  reducedMotion.addEventListener("change", () => {
    if (reducedMotion.matches) {
      stopAnimation();
      return;
    }
    resetPacketState();
    startAnimation();
  });

  resetPacketState();
  startAnimation();
})();
