(() => {
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const button = target.closest("[data-notice-dismiss]");
    if (!(button instanceof HTMLButtonElement)) return;

    const notice = button.closest("[data-engine-notice]");
    if (!(notice instanceof HTMLElement)) return;

    notice.remove();
  });
})();
