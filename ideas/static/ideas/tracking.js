(() => {
  const storageKey = "ideaflow.collapsedTrackingFamilies";
  let collapsed = new Set();
  try {
    collapsed = new Set(JSON.parse(localStorage.getItem(storageKey) || "[]"));
  } catch (_error) {
    collapsed = new Set();
  }

  const rows = [...document.querySelectorAll(".tracking-item[data-idea-id]")];
  const rowsById = new Map(rows.map((row) => [row.dataset.ideaId, row]));

  function refreshVisibility() {
    rows.forEach((row) => {
      let parentId = row.dataset.parentId;
      let hidden = false;
      const visited = new Set();
      while (parentId && !visited.has(parentId)) {
        visited.add(parentId);
        if (collapsed.has(parentId)) {
          hidden = true;
          break;
        }
        parentId = rowsById.get(parentId)?.dataset.parentId;
      }
      row.classList.toggle("family-child-hidden", hidden);
      row.hidden = hidden;
      row.style.display = hidden ? "none" : "";
    });
  }

  function setExpanded(button, expanded) {
    const parentId = button.dataset.familyToggle;
    button.setAttribute("aria-expanded", String(expanded));
    if (expanded) collapsed.delete(parentId);
    else collapsed.add(parentId);
    refreshVisibility();
  }

  document.querySelectorAll("[data-family-toggle]").forEach((button) => {
    const parentId = button.dataset.familyToggle;
    setExpanded(button, !collapsed.has(parentId));
    button.addEventListener("click", () => {
      setExpanded(button, button.getAttribute("aria-expanded") !== "true");
      try {
        localStorage.setItem(storageKey, JSON.stringify([...collapsed]));
      } catch (_error) {
        // Collapsing still works if storage is unavailable.
      }
    });
  });
})();
