(() => {
  const stateForm = document.getElementById("dashboard-layout-state");
  if (!stateForm) {
    return;
  }

  const csrfToken = stateForm.querySelector("input[name='csrfmiddlewaretoken']")?.value;
  const layoutUrl = stateForm.dataset.layoutUrl;
  if (!csrfToken || !layoutUrl) {
    return;
  }

  document.querySelectorAll("[data-widget-grid]").forEach((grid) => {
    let draggedCard = null;

    grid.querySelectorAll("[data-drag-handle]").forEach((handle) => {
      handle.addEventListener("dragstart", (event) => {
        draggedCard = handle.closest("[data-widget-id]");
        if (!draggedCard) {
          return;
        }
        draggedCard.classList.add("is-dragging");
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", draggedCard.dataset.widgetId || "");
        }
      });

      handle.addEventListener("dragend", () => {
        if (!draggedCard) {
          return;
        }
        draggedCard.classList.remove("is-dragging");
        persistOrder(grid);
        draggedCard = null;
      });

      handle.addEventListener("keydown", (event) => {
        const card = handle.closest("[data-widget-id]");
        if (!card || card.parentElement !== grid) {
          return;
        }

        const previousKeys = new Set(["ArrowLeft", "ArrowUp"]);
        const nextKeys = new Set(["ArrowRight", "ArrowDown"]);
        if (!previousKeys.has(event.key) && !nextKeys.has(event.key)) {
          return;
        }

        event.preventDefault();
        const sibling = previousKeys.has(event.key)
          ? card.previousElementSibling
          : card.nextElementSibling;
        if (!sibling) {
          return;
        }

        if (previousKeys.has(event.key)) {
          grid.insertBefore(card, sibling);
        } else {
          grid.insertBefore(sibling, card);
        }
        persistOrder(grid);
      });
    });

    grid.addEventListener("dragover", (event) => {
      if (!draggedCard) {
        return;
      }
      event.preventDefault();
      const target = event.target.closest("[data-widget-id]");
      if (!target || target === draggedCard || target.parentElement !== grid) {
        return;
      }

      const targetRect = target.getBoundingClientRect();
      const draggedRect = draggedCard.getBoundingClientRect();
      const isSameRow =
        Math.abs(targetRect.top - draggedRect.top) <
        Math.min(targetRect.height, draggedRect.height) / 2;
      const insertAfter = isSameRow
        ? event.clientX > targetRect.left + targetRect.width / 2
        : event.clientY > targetRect.top + targetRect.height / 2;
      grid.insertBefore(draggedCard, insertAfter ? target.nextSibling : target);
    });
  });

  async function persistOrder(grid) {
    const body = new URLSearchParams();
    body.append("csrfmiddlewaretoken", csrfToken);
    body.append("plane", grid.dataset.plane || "");
    grid.querySelectorAll("[data-widget-id]").forEach((card) => {
      body.append("order", card.dataset.widgetId || "");
    });

    try {
      const response = await fetch(layoutUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
        body: body.toString(),
      });
      if (!response.ok) {
        throw new Error(`Dashboard layout save failed with ${response.status}`);
      }
    } catch (error) {
      console.error(error);
    }
  }
})();
