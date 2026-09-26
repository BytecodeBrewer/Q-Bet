(() => {
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const sidebar = document.getElementById("app-sidebar");
  if (!toggle || !sidebar) return;

  const closeButton = sidebar.querySelector("[data-sidebar-close]");
  const backdrop = document.querySelector("[data-sidebar-backdrop]");
  const accountRoot = document.querySelector("[data-account-menu]");
  const accountToggle = accountRoot?.querySelector("[data-account-menu-toggle]");
  const accountPanel = accountRoot?.querySelector("[data-account-menu-panel]");
  const storageKey = "qbet.sidebar.collapsed";

  const persistSidebarState = (collapsed) => {
    try {
      window.localStorage.setItem(storageKey, collapsed ? "1" : "0");
    } catch (_) {
      // Navigation remains functional even when storage is unavailable.
    }
  };

  const applySidebarState = (collapsed, { persist = false, restoreFocus = false } = {}) => {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggle.setAttribute(
      "aria-label",
      collapsed ? "Open application navigation" : "Close application navigation",
    );
    sidebar.setAttribute("aria-hidden", collapsed ? "true" : "false");
    if (persist) persistSidebarState(collapsed);
    if (restoreFocus && collapsed && typeof toggle.focus === "function") toggle.focus();
  };

  const readInitialSidebarState = () => {
    try {
      return window.localStorage.getItem(storageKey) !== "0";
    } catch (_) {
      return true;
    }
  };

  const closeAccountMenu = ({ restoreFocus = false } = {}) => {
    if (!accountToggle || !accountPanel) return;
    accountPanel.hidden = true;
    accountToggle.setAttribute("aria-expanded", "false");
    if (restoreFocus && typeof accountToggle.focus === "function") accountToggle.focus();
  };

  applySidebarState(readInitialSidebarState());

  toggle.addEventListener("click", () => {
    const collapsed = document.body.classList.contains("sidebar-collapsed");
    applySidebarState(!collapsed, { persist: true });
  });

  closeButton?.addEventListener("click", () => {
    applySidebarState(true, { persist: true, restoreFocus: true });
  });

  backdrop?.addEventListener("click", () => {
    applySidebarState(true, { persist: true, restoreFocus: true });
  });

  if (accountToggle && accountPanel) {
    accountToggle.addEventListener("click", () => {
      const opening = accountPanel.hidden;
      if (opening && !document.body.classList.contains("sidebar-collapsed")) {
        applySidebarState(true, { persist: true });
      }
      accountPanel.hidden = !opening;
      accountToggle.setAttribute("aria-expanded", opening ? "true" : "false");
      if (opening) {
        const first = accountPanel.querySelector('[role="menuitem"]');
        if (first instanceof HTMLElement) first.focus();
      }
    });

    document.addEventListener("click", (event) => {
      if (accountRoot && !accountRoot.contains(event.target)) closeAccountMenu();
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;

    if (!document.body.classList.contains("sidebar-collapsed")) {
      applySidebarState(true, { persist: true, restoreFocus: true });
    }

    if (accountPanel && !accountPanel.hidden) {
      closeAccountMenu({ restoreFocus: true });
    }
  });
})();
