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
    let startClientX = 0;
    let startClientY = 0;
    let layoutCompensationX = 0;
    let layoutCompensationY = 0;
    let initialOrder = [];
    let lastClientX = 0;
    let lastClientY = 0;
    let finishing = false;

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
        startClientX = event.clientX;
        startClientY = event.clientY;
        layoutCompensationX = 0;
        layoutCompensationY = 0;
        initialOrder = dnd.order(grid);
        lastClientX = event.clientX;
        lastClientY = event.clientY;
        finishing = false;
        handle.setPointerCapture(event.pointerId);
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
          grid.classList.add("is-reordering");
          draggedCard.classList.add("is-dragging");
          draggedCard.style.transition = "none";
        }

        positionDraggedCard(draggedCard, event.clientX, event.clientY);
        const reordered = moveCardTowardPointer(
          grid,
          draggedCard,
          event.clientX,
          event.clientY,
          dragAxis,
        );
        if (reordered) {
          positionDraggedCard(draggedCard, event.clientX, event.clientY);
        }
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

        if (dragStarted && !commit) {
          dnd.restoreOrder(grid, before);
        }

        card.classList.remove("is-dragging");
        card.style.removeProperty("--drag-x");
        card.style.removeProperty("--drag-y");
        card.style.removeProperty("transition");
        grid.classList.remove("is-reordering");

        const after = dnd.order(grid);
        const shouldPersist = dragStarted && commit && dnd.orderChanged(before, after);

        draggedCard = null;
        activeHandle = null;
        activePointerId = null;
        dragStarted = false;
        dragAxis = "free";
        startClientX = 0;
        startClientY = 0;
        layoutCompensationX = 0;
        layoutCompensationY = 0;
        initialOrder = [];
        lastClientX = 0;
        lastClientY = 0;

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

        if (event.key === "Escape" && draggedCard && activeHandle === handle && activePointerId !== null) {
          event.preventDefault();
          finishPointerDrag({ pointerId: activePointerId });
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
      const translateX = dragAxis === "y" ? 0 : pointerDeltaX + layoutCompensationX;
      const translateY = pointerDeltaY + layoutCompensationY;
      card.style.setProperty("--drag-x", `${translateX}px`);
      card.style.setProperty("--drag-y", `${translateY}px`);
    }

    function moveCardTowardPointer(
      activeGrid,
      card,
      clientX,
      clientY,
      axis,
    ) {
      const orderedCards = Array.from(
        activeGrid.querySelectorAll("[data-widget-id]"),
      );
      const currentIndex = orderedCards.indexOf(card);
      const candidates = orderedCards.filter((candidate) => candidate !== card);
      if (currentIndex < 0 || !candidates.length) {
        return false;
      }

      const target = candidates.reduce((closest, candidate) => {
        const rect = candidate.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;
        const distance = axis === "y"
          ? Math.abs(clientY - centerY)
          : Math.hypot(clientX - centerX, clientY - centerY);
        return !closest || distance < closest.distance
          ? { card: candidate, rect, distance }
          : closest;
      }, null);

      if (!target) {
        return false;
      }

      const targetIndex = orderedCards.indexOf(target.card);
      if (targetIndex < 0) {
        return false;
      }

      let reference = null;
      if (axis === "y") {
        const targetCenterY = target.rect.top + target.rect.height / 2;
        if (targetIndex < currentIndex) {
          if (clientY > targetCenterY - REORDER_HYSTERESIS) {
            return false;
          }
          reference = target.card;
        } else {
          if (clientY < targetCenterY + REORDER_HYSTERESIS) {
            return false;
          }
          reference = target.card.nextElementSibling;
        }
      } else {
        const sameRow =
          Math.abs(target.card.offsetTop - card.offsetTop) <
          Math.min(target.card.offsetHeight, card.offsetHeight) / 2;
        const movingBackward = targetIndex < currentIndex;
        if (sameRow) {
          const targetCenterX = target.rect.left + target.rect.width / 2;
          if (movingBackward) {
            if (clientX > targetCenterX - REORDER_HYSTERESIS) {
              return false;
            }
            reference = target.card;
          } else {
            if (clientX < targetCenterX + REORDER_HYSTERESIS) {
              return false;
            }
            reference = target.card.nextElementSibling;
          }
        } else {
          const targetCenterY = target.rect.top + target.rect.height / 2;
          if (movingBackward) {
            if (clientY > targetCenterY - REORDER_HYSTERESIS) {
              return false;
            }
            reference = target.card;
          } else {
            if (clientY < targetCenterY + REORDER_HYSTERESIS) {
              return false;
            }
            reference = target.card.nextElementSibling;
          }
        }
      }

      if (
        reference === card ||
        (!reference && card === activeGrid.lastElementChild)
      ) {
        return false;
      }

      const before = capturePositions(activeGrid);
      const draggedBefore = card.getBoundingClientRect();
      activeGrid.insertBefore(card, reference);
      const draggedAfter = card.getBoundingClientRect();
      layoutCompensationX += draggedBefore.left - draggedAfter.left;
      layoutCompensationY += draggedBefore.top - draggedAfter.top;
      animateReflow(activeGrid, before, card);
      return true;
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

  function capturePositions(grid) {
    return new Map(
      Array.from(grid.querySelectorAll("[data-widget-id]"))
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
