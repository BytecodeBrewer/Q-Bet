(() => {
  const dialog = document.getElementById("provider-balance-editor");
  const csrfInput = dialog?.querySelector("[name=csrfmiddlewaretoken]");

  const formatMoney = (amount, currency) => {
    const locale = document.documentElement.lang || navigator.language || "en";
    const value = Number(amount);
    if (!Number.isFinite(value)) {
      return `${amount} ${currency}`;
    }
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  };

  const formatObservedAt = (value) => {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return "Updated now";
    }
    const locale = document.documentElement.lang || navigator.language || "en";
    return `Updated ${new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(parsed)}`;
  };

  document.querySelectorAll("[data-provider-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!dialog) return;
      const provider = dialog.querySelector("[name=provider]");
      const currency = dialog.querySelector("[name=currency]");
      const amount = dialog.querySelector("[name=amount]");
      const note = dialog.querySelector("[name=note]");
      const name = document.getElementById("provider-editor-name");

      if (provider) provider.value = button.dataset.providerId || "";
      if (currency) currency.value = button.dataset.currency || "";
      if (amount) amount.value = button.dataset.amount || "0";
      if (note) note.value = button.dataset.note || "";
      if (name) {
        name.textContent = `${button.dataset.providerLabel || "Provider"} · ${button.dataset.currency || ""}`;
      }
      dialog.showModal();
      amount?.focus();
    });
  });

  dialog?.querySelectorAll("[data-provider-editor-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });

  document.querySelectorAll("[data-central-refresh]").forEach((button) => {
    button.addEventListener("click", async () => {
      const card = button.closest(".portfolio-location-central");
      if (!card || !csrfInput) return;

      const amount = card.querySelector("[data-central-amount]");
      const updated = card.querySelector("[data-central-updated]");
      const source = card.querySelector("[data-central-source]");
      const message = card.querySelector("[data-central-refresh-message]");
      const originalLabel = button.textContent;

      button.disabled = true;
      button.textContent = "Refreshing…";
      if (message) message.textContent = "Reading bunq balance…";

      try {
        const body = new URLSearchParams({ currency: button.dataset.currency || "" });
        const response = await fetch(button.dataset.refreshUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "X-CSRFToken": csrfInput.value,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          },
          body,
        });
        const payload = await response.json();
        if (!response.ok || payload.status !== "ok") {
          throw new Error(payload.message || "Balance refresh failed.");
        }

        if (amount) amount.textContent = formatMoney(payload.amount, payload.currency);
        if (updated) updated.textContent = formatObservedAt(payload.observed_at);
        if (source) source.textContent = "bunq";
        if (message) message.textContent = "Balance refreshed from bunq. No transfer was made.";
      } catch (error) {
        if (message) {
          message.textContent =
            error instanceof Error ? error.message : "Balance refresh failed.";
        }
      } finally {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    });
  });

  const filter = document.querySelector("[data-provider-filter]");
  const cards = Array.from(document.querySelectorAll("[data-provider-card]"));
  const empty = document.querySelector("[data-provider-filter-empty]");

  filter?.addEventListener("input", () => {
    const term = filter.value.trim().toLowerCase();
    let visible = 0;
    cards.forEach((card) => {
      const matches = !term || (card.dataset.providerName || "").includes(term);
      card.hidden = !matches;
      if (matches) visible += 1;
    });
    if (empty) empty.hidden = visible !== 0;
  });
})();
