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

  document.querySelectorAll("[data-widget-grid]").forEach((grid) => {
    let draggedCard = null;
    let activeHandle = null;
    let activePointerId = null;
    let pointerOffsetX = 0;
    let pointerOffsetY = 0;
    let dragTranslateX = 0;
    let dragTranslateY = 0;

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
        const rect = card.getBoundingClientRect();
        draggedCard = card;
        activeHandle = handle;
        activePointerId = event.pointerId;
        pointerOffsetX = event.clientX - rect.left;
        pointerOffsetY = event.clientY - rect.top;
        dragTranslateX = 0;
        dragTranslateY = 0;
        card.style.setProperty("--drag-x", "0px");
        card.style.setProperty("--drag-y", "0px");
        handle.setPointerCapture(event.pointerId);
        grid.classList.add("is-reordering");
        card.classList.add("is-dragging");
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
        positionDraggedCard(draggedCard, event.clientX, event.clientY);
        moveCardTowardPointer(grid, draggedCard, event.clientX, event.clientY);
        positionDraggedCard(draggedCard, event.clientX, event.clientY);
      });

      const finishPointerDrag = (event) => {
        if (
          !draggedCard ||
          activeHandle !== handle ||
          activePointerId !== event.pointerId
        ) {
          return;
        }
        if (handle.hasPointerCapture(event.pointerId)) {
          handle.releasePointerCapture(event.pointerId);
        }
        draggedCard.classList.remove("is-dragging");
        draggedCard.style.removeProperty("--drag-x");
        draggedCard.style.removeProperty("--drag-y");
        grid.classList.remove("is-reordering");
        persistOrder(grid);
        draggedCard = null;
        activeHandle = null;
        activePointerId = null;
        dragTranslateX = 0;
        dragTranslateY = 0;
      };

      handle.addEventListener("pointerup", finishPointerDrag);
      handle.addEventListener("pointercancel", finishPointerDrag);

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
      const rect = card.getBoundingClientRect();
      const layoutLeft = rect.left - dragTranslateX;
      const layoutTop = rect.top - dragTranslateY;
      dragTranslateX = clientX - pointerOffsetX - layoutLeft;
      dragTranslateY = clientY - pointerOffsetY - layoutTop;
      card.style.setProperty("--drag-x", `${dragTranslateX}px`);
      card.style.setProperty("--drag-y", `${dragTranslateY}px`);
    }
  });

  function moveCardTowardPointer(grid, draggedCard, clientX, clientY) {
    const candidates = Array.from(grid.querySelectorAll("[data-widget-id]"))
      .filter((card) => card !== draggedCard);
    if (!candidates.length) {
      return;
    }

    const target = candidates.reduce((closest, candidate) => {
      const rect = candidate.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const distance = Math.hypot(clientX - centerX, clientY - centerY);
      return !closest || distance < closest.distance
        ? { card: candidate, rect, distance }
        : closest;
    }, null);

    if (!target) {
      return;
    }

    const withinTarget =
      clientX >= target.rect.left - 24 &&
      clientX <= target.rect.right + 24 &&
      clientY >= target.rect.top - 24 &&
      clientY <= target.rect.bottom + 24;
    if (!withinTarget) {
      return;
    }

    // offsetTop/offsetHeight describe layout geometry and ignore the CSS transform
    // used to keep the dragged card under the pointer. That keeps vertical reordering
    // correct in single-column and narrow layouts.
    const sameRow =
      Math.abs(target.card.offsetTop - draggedCard.offsetTop) <
      Math.min(target.card.offsetHeight, draggedCard.offsetHeight) / 2;
    const insertAfter = sameRow
      ? clientX > target.rect.left + target.rect.width / 2
      : clientY > target.rect.top + target.rect.height / 2;

    const reference = insertAfter ? target.card.nextSibling : target.card;
    if (reference === draggedCard || (!reference && draggedCard === grid.lastElementChild)) {
      return;
    }

    const before = capturePositions(grid);
    grid.insertBefore(draggedCard, reference);
    animateReflow(grid, before, draggedCard);
  }

  function capturePositions(grid) {
    return new Map(
      Array.from(grid.querySelectorAll("[data-widget-id]"))
        .map((card) => [card, card.getBoundingClientRect()])
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
