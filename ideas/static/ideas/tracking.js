(() => {
  const filterForm = document.querySelector("[data-auto-submit-filters]");
  if (filterForm) {
    let searchTimer;
    const applyButton = filterForm.querySelector("[data-filter-apply]");
    if (applyButton) applyButton.hidden = true;
    const applyFilters = () => {
      clearTimeout(searchTimer);
      filterForm.submit();
    };
    filterForm.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", applyFilters);
    });
    const search = filterForm.querySelector("input[type='search']");
    if (search) {
      search.addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(applyFilters, 400);
      });
    }
    filterForm.addEventListener("submit", () => clearTimeout(searchTimer));
  }

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

  document.querySelectorAll("[data-tracking-status-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (!payload.ok) throw new Error("Archive was not confirmed");
        const row = form.closest(".tracking-item");
        if (row) {
          rowsById.delete(row.dataset.ideaId);
          collapsed.delete(row.dataset.ideaId);
          row.remove();
          refreshVisibility();
        }
      } catch (_error) {
        if (button) button.disabled = false;
        form.submit();
      }
    });
  });
})();
