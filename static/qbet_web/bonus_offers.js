(() => {
  const menuRoot = document.querySelector("[data-bonus-offer-menu]");
  const menuToggle = menuRoot?.querySelector("[data-bonus-offer-menu-toggle]");
  const menuPanel = menuRoot?.querySelector("[data-bonus-offer-menu-panel]");
  const dialog = document.querySelector("[data-bonus-offer-dialog]");

  const closeMenu = () => {
    if (!menuToggle || !menuPanel) return;
    menuPanel.hidden = true;
    menuToggle.setAttribute("aria-expanded", "false");
  };

  const openDialog = () => {
    closeMenu();
    if (!(dialog instanceof HTMLDialogElement)) return;
    if (!dialog.open) dialog.showModal();
    const first = dialog.querySelector("select, input, textarea, button");
    if (first instanceof HTMLElement) first.focus();
  };

  if (menuToggle && menuPanel) {
    menuToggle.addEventListener("click", () => {
      const opening = menuPanel.hidden;
      menuPanel.hidden = !opening;
      menuToggle.setAttribute("aria-expanded", opening ? "true" : "false");
      if (opening) {
        const first = menuPanel.querySelector('[role="menuitem"]');
        if (first instanceof HTMLElement) first.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (menuRoot && !menuRoot.contains(event.target)) closeMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !menuPanel.hidden) {
        closeMenu();
        menuToggle.focus();
      }
    });
  }

  document.querySelectorAll("[data-bonus-offer-dialog-open]").forEach((button) => {
    button.addEventListener("click", openDialog);
  });

  if (dialog instanceof HTMLDialogElement) {
    dialog.querySelectorAll("[data-bonus-offer-dialog-close]").forEach((button) => {
      button.addEventListener("click", () => dialog.close());
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    if (dialog.getAttribute("data-open") === "true") {
      openDialog();
    }
  }

  const syncPromotionFields = (form) => {
    const promotionShape = form.querySelector('[name="promotion_shape"]');
    const sections = [...form.querySelectorAll("[data-bonus-offer-shapes]")];
    const sync = () => {
      const selected = promotionShape instanceof HTMLSelectElement ? promotionShape.value : "";
      sections.forEach((section) => {
        const shapes = (section.getAttribute("data-bonus-offer-shapes") || "")
          .split(/\s+/)
          .filter(Boolean);
        const active = shapes.includes(selected);
        section.hidden = !active;
        section.querySelectorAll("input, select, textarea").forEach((field) => {
          field.disabled = !active;
        });
      });
    };
    if (promotionShape) promotionShape.addEventListener("change", sync);
    sync();
  };

  document.querySelectorAll("[data-bonus-offer-form]").forEach(syncPromotionFields);
})();
