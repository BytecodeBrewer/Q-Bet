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

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const dnd = globalThis.QBetDashboardDnd;
  if (!dnd) {
    return;
  }
  const DRAG_START_DISTANCE = 10;
  const REORDER_HYSTERESIS = 10;

  document.querySelectorAll("[data-widget-grid]").forEach((grid) => {
    let draggedCard = null;
    let activeHandle = null;
    let activePointerId = null;
    let dragStarted = false;
    let dragAxis = "free";
    let placeholder = null;
    let originalStyle = null;
    let startClientX = 0;
    let startClientY = 0;
    let initialOrder = [];
    let lastClientX = 0;
    let lastClientY = 0;
    let finishing = false;
    let cancelActivePointerDrag = null;

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && cancelActivePointerDrag) {
        event.preventDefault();
        cancelActivePointerDrag();
      }
    }, true);

    grid.querySelectorAll("[data-drag-handle]").forEach((handle) => {
      handle.addEventListener("pointerdown", (event) => {
        if (event.pointerType === "mouse" && event.button !== 0) {
          return;
        }
        const card = handle.closest("[data-widget-id]");
        if (!card || card.parentElement !== grid) {
          return;
        }

        event.preventDefault();
        draggedCard = card;
        activeHandle = handle;
        activePointerId = event.pointerId;
        dragStarted = false;
        dragAxis = isSingleColumn(grid) ? "y" : "free";
        placeholder = null;
        originalStyle = card.getAttribute("style");
        startClientX = event.clientX;
        startClientY = event.clientY;
        initialOrder = dnd.order(grid);
        lastClientX = event.clientX;
        lastClientY = event.clientY;
        finishing = false;
        handle.setPointerCapture(event.pointerId);
        cancelActivePointerDrag = () => {
          if (
            draggedCard &&
            activeHandle === handle &&
            activePointerId !== null
          ) {
            finishPointerDrag({ pointerId: activePointerId });
          }
        };
      });

      handle.addEventListener("pointermove", (event) => {
        if (
          !draggedCard ||
          activeHandle !== handle ||
          activePointerId !== event.pointerId
        ) {
          return;
        }

        event.preventDefault();
        lastClientX = event.clientX;
        lastClientY = event.clientY;
        const deltaX = event.clientX - startClientX;
        const deltaY = event.clientY - startClientY;

        if (!dragStarted) {
          if (Math.hypot(deltaX, deltaY) < DRAG_START_DISTANCE) {
            return;
          }
          dragStarted = true;
          const startRect = draggedCard.getBoundingClientRect();
          grid.classList.add("is-reordering");
          draggedCard.classList.add("is-dragging");
          liftCard(draggedCard, startRect);
        }

        positionDraggedCard(draggedCard, event.clientX, event.clientY);
        movePlaceholderTowardPointer(
          grid,
          placeholder,
          draggedCard,
          event.clientX,
          event.clientY,
          dragAxis,
        );
      });

      const finishPointerDrag = (event, { commit = false } = {}) => {
        if (
          finishing ||
          !draggedCard ||
          activeHandle !== handle ||
          activePointerId !== event.pointerId
        ) {
          return;
        }
        finishing = true;
        const pointerId = activePointerId;
        const card = draggedCard;
        const before = initialOrder;

        if (dragStarted && placeholder?.parentElement === grid) {
          grid.insertBefore(card, placeholder);
        }
        if (dragStarted && !commit) {
          dnd.restoreOrder(grid, before);
        }
        placeholder?.remove();
        placeholder = null;

        card.classList.remove("is-dragging");
        if (originalStyle === null) {
          card.removeAttribute("style");
        } else {
          card.setAttribute("style", originalStyle);
        }
        originalStyle = null;
        grid.classList.remove("is-reordering");

        const after = dnd.order(grid);
        const shouldPersist = dnd.shouldPersist({
          dragStarted,
          commit,
          before,
          after,
        });

        draggedCard = null;
        activeHandle = null;
        activePointerId = null;
        dragStarted = false;
        dragAxis = "free";
        startClientX = 0;
        startClientY = 0;
        initialOrder = [];
        lastClientX = 0;
        lastClientY = 0;
        cancelActivePointerDrag = null;

        if (handle.hasPointerCapture(pointerId)) {
          handle.releasePointerCapture(pointerId);
        }
        finishing = false;

        if (shouldPersist) {
          persistOrder(grid);
        }
      };

      handle.addEventListener("pointerup", (event) => {
        lastClientX = event.clientX;
        lastClientY = event.clientY;
        const commit = dragStarted &&
          dnd.pointInside(grid.getBoundingClientRect(), lastClientX, lastClientY);
        finishPointerDrag(event, { commit });
      });
      handle.addEventListener("pointercancel", (event) => {
        finishPointerDrag(event);
      });
      handle.addEventListener("lostpointercapture", (event) => {
        finishPointerDrag(event);
      });
      window.addEventListener("blur", () => {
        if (draggedCard && activeHandle === handle && activePointerId !== null) {
          finishPointerDrag({ pointerId: activePointerId });
        }
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

        const before = capturePositions(grid);
        if (previousKeys.has(event.key)) {
          grid.insertBefore(card, sibling);
        } else {
          grid.insertBefore(sibling, card);
        }
        animateReflow(grid, before, card);
        persistOrder(grid);
      });
    });

    function positionDraggedCard(card, clientX, clientY) {
      const pointerDeltaX = clientX - startClientX;
      const pointerDeltaY = clientY - startClientY;
      const translateX = dragAxis === "y" ? 0 : pointerDeltaX;
      card.style.setProperty("--drag-x", `${translateX}px`);
      card.style.setProperty("--drag-y", `${pointerDeltaY}px`);
    }

    function liftCard(card, rect) {
      placeholder = document.createElement("div");
      placeholder.className = "dashboard-drag-placeholder";
      placeholder.setAttribute("aria-hidden", "true");
      placeholder.style.height = `${rect.height}px`;
      grid.insertBefore(placeholder, card);
      document.body.appendChild(card);

      card.style.position = "fixed";
      card.style.left = `${rect.left}px`;
      card.style.top = `${rect.top}px`;
      card.style.width = `${rect.width}px`;
      card.style.margin = "0";
    }

    function movePlaceholderTowardPointer(
      activeGrid,
      activePlaceholder,
      card,
      clientX,
      clientY,
      axis,
    ) {
      const cards = Array.from(activeGrid.querySelectorAll("[data-widget-id]"))
        .filter((candidate) => candidate !== card)
        .map((candidate) => ({ card: candidate, rect: candidate.getBoundingClientRect() }));
      const slot = dnd.closestInsertionSlot(
        { x: clientX, y: clientY },
        activePlaceholder.getBoundingClientRect(),
        cards,
        REORDER_HYSTERESIS,
      );
      if (!slot || (axis === "y" && slot.axis !== "y")) {
        return false;
      }

      const before = capturePositions(activeGrid, card);
      const moved = dnd.movePlaceholder(
        activeGrid,
        activePlaceholder,
        slot.card,
        slot.position,
        card,
      );
      if (moved) animateReflow(activeGrid, before, card);
      return moved;
    }
  });

  function isSingleColumn(grid) {
    const cards = Array.from(grid.querySelectorAll("[data-widget-id]"));
    if (cards.length < 2) {
      return true;
    }
    const firstTop = cards[0].offsetTop;
    return !cards.slice(1).some(
      (card) => Math.abs(card.offsetTop - firstTop) < card.offsetHeight / 2,
    );
  }

  function capturePositions(grid, excludedCard = null) {
    return new Map(
      Array.from(grid.querySelectorAll("[data-widget-id]"))
        .filter((card) => card !== excludedCard)
        .map((card) => [card, card.getBoundingClientRect()]),
    );
  }

  function animateReflow(grid, before, draggedCard) {
    if (reduceMotion.matches) {
      return;
    }
    grid.querySelectorAll("[data-widget-id]").forEach((card) => {
      if (card === draggedCard) {
        return;
      }
      const previous = before.get(card);
      if (!previous) {
        return;
      }
      const current = card.getBoundingClientRect();
      const deltaX = previous.left - current.left;
      const deltaY = previous.top - current.top;
      if (Math.abs(deltaX) < 1 && Math.abs(deltaY) < 1) {
        return;
      }
      card.animate(
        [
          { transform: `translate(${deltaX}px, ${deltaY}px)` },
          { transform: "translate(0, 0)" },
        ],
        {
          duration: 190,
          easing: "cubic-bezier(.2,.8,.2,1)",
        },
      );
    });
  }

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
