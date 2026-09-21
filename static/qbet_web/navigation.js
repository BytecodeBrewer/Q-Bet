(() => {
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const sidebar = document.getElementById("app-sidebar");
  if (!toggle || !sidebar) return;

  const storageKey = "qbet.sidebar.collapsed";
  const apply = (collapsed) => {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  };

  let collapsed = false;
  try {
    collapsed = window.localStorage.getItem(storageKey) === "1";
  } catch (_) {
    collapsed = false;
  }
  apply(collapsed);

  toggle.addEventListener("click", () => {
    collapsed = !document.body.classList.contains("sidebar-collapsed");
    apply(collapsed);
    try {
      window.localStorage.setItem(storageKey, collapsed ? "1" : "0");
    } catch (_) {
      // Navigation remains functional even when storage is unavailable.
    }
  });
})();
