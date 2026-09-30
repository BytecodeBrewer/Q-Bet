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
    shouldPersist({ dragStarted, commit, before, after }) {
      return Boolean(dragStarted && commit && api.orderChanged(before, after));
    },
    closestInsertionSlot(point, slotRect, candidates, hysteresis = 0) {
      if (!candidates.length) return null;

      const target = candidates.reduce((closest, candidate) => {
        const rect = candidate.rect;
        const dx = Math.max(rect.left - point.x, 0, point.x - rect.right);
        const dy = Math.max(rect.top - point.y, 0, point.y - rect.bottom);
        const distance = Math.hypot(dx, dy);
        return !closest || distance < closest.distance
          ? { ...candidate, distance }
          : closest;
      }, null);

      const slotCenterY = slotRect.top + slotRect.height / 2;
      const targetCenterY = target.rect.top + target.rect.height / 2;
      const sameRow = Math.abs(slotCenterY - targetCenterY) <
        Math.min(slotRect.height, target.rect.height) / 2;
      const axis = sameRow ? "x" : "y";
      const midpoint = axis === "x"
        ? target.rect.left + target.rect.width / 2
        : target.rect.top + target.rect.height / 2;
      const coordinate = axis === "x" ? point.x : point.y;
      if (coordinate < midpoint - hysteresis) {
        return { card: target.card, position: "before", axis };
      }
      if (coordinate > midpoint + hysteresis) {
        return { card: target.card, position: "after", axis };
      }
      return null;
    },
    movePlaceholder(grid, placeholder, card, position, activeCard) {
      const cards = Array.from(grid.querySelectorAll("[data-widget-id]"))
        .filter((item) => item !== activeCard);
      const targetIndex = cards.indexOf(card);
      if (targetIndex < 0) return false;

      const reference = position === "before" ? card : cards[targetIndex + 1] || null;
      const flowChildren = Array.from(grid.children)
        .filter((item) => item === placeholder || cards.includes(item));
      const placeholderIndex = flowChildren.indexOf(placeholder);
      if (placeholderIndex < 0 || (flowChildren[placeholderIndex + 1] || null) === reference) {
        return false;
      }

      grid.insertBefore(placeholder, reference);
      return true;
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
