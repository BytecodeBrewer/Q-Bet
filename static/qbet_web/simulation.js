(() => {
  const container = document.querySelector("[data-simulation-control]");
  if (!container) return;

  const csrfToken = container.querySelector("input[name='csrfmiddlewaretoken']")?.value;
  const status = container.querySelector("[data-simulation-client-status]");
  const autoRun = container.querySelector("[data-auto-run-url]");
  const providerActivity = container.querySelector("[data-provider-activity]");
  const providerLabel = container.querySelector("[data-provider-label]");
  const providerActivityUrl = autoRun?.dataset.providerActivityUrl || "";

  const setProviderState = (state, label) => {
    if (!providerActivity || !providerLabel) return;
    Array.from(providerActivity.classList)
      .filter((name) => name.startsWith("activity-") && name !== "activity-state")
      .forEach((name) => providerActivity.classList.remove(name));
    providerActivity.classList.add("activity-" + state);
    providerLabel.textContent = label;
  };

  const setStatus = (message, state = "") => {
    if (!status) return;
    status.textContent = message;
    status.dataset.state = state;
  };

  const refreshProviderActivity = async () => {
    if (!providerActivityUrl) return;
    try {
      const response = await fetch(providerActivityUrl, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      setProviderState(payload.state, payload.label);
    } catch (_error) {
      // Provider activity is observational; a polling failure must not affect Simulation.
    }
  };

  const post = async (url) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-CSRFToken": csrfToken || "",
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || ("Request failed with " + response.status));
    }
    return payload;
  };

  container.querySelectorAll("[data-simulation-stop-url]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      setStatus("Stop requested. The current safe step may finish first.", "working");
      try {
        const payload = await post(button.dataset.simulationStopUrl);
        setStatus("Simulation " + payload.status + ".", payload.status);
        window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        button.disabled = false;
        setStatus(error.message, "error");
      }
    });
  });

  if (!autoRun || !csrfToken) return;

  const url = autoRun.dataset.autoRunUrl;
  window.history.replaceState({}, "", window.location.pathname);
  setStatus("Simulation is running. Progress is persisted after every safe step.", "working");
  void refreshProviderActivity();
  const providerPoll = window.setInterval(refreshProviderActivity, 500);

  post(url)
    .then(async (payload) => {
      window.clearInterval(providerPoll);
      await refreshProviderActivity();
      setStatus("Simulation " + payload.status + ".", payload.status);
      if (payload.report_url) {
        window.location.assign(payload.report_url);
      } else {
        window.location.reload();
      }
    })
    .catch(async (error) => {
      window.clearInterval(providerPoll);
      await refreshProviderActivity();
      setStatus(error.message, "error");
      autoRun.removeAttribute("data-auto-run-url");
    });
})();
