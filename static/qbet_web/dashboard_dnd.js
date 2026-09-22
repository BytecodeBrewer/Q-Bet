(() => {
  const api = {
    pointInside(rect, clientX, clientY) {
      return (
        clientX >= rect.left &&
        clientX <= rect.right &&
        clientY >= rect.top &&
        clientY <= rect.bottom
      );
    },
    order(grid) {
      return Array.from(grid.querySelectorAll("[data-widget-id]"))
        .map((card) => card.dataset.widgetId || "");
    },
    orderChanged(before, after) {
      return before.length !== after.length ||
        before.some((value, index) => value !== after[index]);
    },
    restoreOrder(grid, order) {
      const byId = new Map(
        Array.from(grid.querySelectorAll("[data-widget-id]"))
          .map((card) => [card.dataset.widgetId || "", card]),
      );
      order.forEach((id) => {
        const card = byId.get(id);
        if (card) grid.appendChild(card);
      });
    },
  };

  globalThis.QBetDashboardDnd = api;
})();
