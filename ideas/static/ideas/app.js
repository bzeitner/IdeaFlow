(() => {
  const scrollKey = "ideaflow.pendingScrollRestore";
  const pageUrl = `${window.location.pathname}${window.location.search}`;
  let actionStarted = false;

  try {
    const saved = JSON.parse(sessionStorage.getItem(scrollKey) || "null");
    sessionStorage.removeItem(scrollKey);
    if (saved && saved.url === pageUrl && Date.now() - saved.savedAt < 30000) {
      requestAnimationFrame(() => window.scrollTo(saved.x, saved.y));
    }
  } catch (_error) {
    // Actions still work if session storage is unavailable.
  }

  document.addEventListener("submit", () => { actionStarted = true; }, true);
  document.addEventListener("change", (event) => {
    if (event.target.form) actionStarted = true;
  }, true);

  window.addEventListener("pagehide", () => {
    if (!actionStarted) return;
    try {
      sessionStorage.setItem(scrollKey, JSON.stringify({
        url: pageUrl,
        x: window.scrollX,
        y: window.scrollY,
        savedAt: Date.now(),
      }));
    } catch (_error) {
      // Navigation should never be blocked by scroll restoration.
    }
  });

  document.querySelectorAll("[data-repeat-result-form]").forEach((form) => {
    const select = form.querySelector("select[name='status']");
    const saveStatus = form.querySelector("[data-save-status]");
    let savedValue = select.value;

    select.addEventListener("change", async () => {
      const requestedValue = select.value;
      const formData = new FormData(form);
      select.disabled = true;
      saveStatus.textContent = " Saving…";
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: formData,
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || `HTTP ${response.status}`);
        }
        savedValue = requestedValue;
        saveStatus.textContent = " Saved";
      } catch (_error) {
        select.value = savedValue;
        saveStatus.textContent = " Could not save";
      } finally {
        select.disabled = false;
        actionStarted = false;
        form.dispatchEvent(new CustomEvent("repeat-results-refresh", { bubbles: true }));
      }
    });
  });

  document.querySelectorAll("[data-repeat-results-panel]").forEach((panel) => {
    const search = panel.querySelector("[data-repeat-results-search]");
    const status = panel.querySelector("[data-repeat-results-status]");
    const sort = panel.querySelector("[data-repeat-results-sort]");
    const body = panel.querySelector("[data-repeat-results-body]");
    const count = panel.querySelector("[data-repeat-results-count]");
    const empty = panel.querySelector("[data-repeat-results-empty]");
    const rows = Array.from(body.querySelectorAll("[data-repeat-result-row]"));

    const rowStatus = (row) => {
      const select = row.querySelector("select[name='status']");
      return select ? select.value : row.querySelector("[data-repeat-result-status]").dataset.repeatResultStatus;
    };
    const compareRows = (left, right) => {
      const [field, direction] = sort.value.split("-");
      let comparison;
      if (field === "found") comparison = Number(left.dataset.found) - Number(right.dataset.found);
      else if (field === "title") comparison = left.dataset.title.localeCompare(right.dataset.title);
      else comparison = rowStatus(left).localeCompare(rowStatus(right));
      return direction === "desc" ? -comparison : comparison;
    };
    const updateResults = () => {
      const query = search.value.trim().toLocaleLowerCase();
      let visible = 0;
      rows.sort(compareRows).forEach((row) => {
        const matchesSearch = !query || row.textContent.toLocaleLowerCase().includes(query);
        const matchesStatus = !status.value || rowStatus(row) === status.value;
        row.hidden = !(matchesSearch && matchesStatus);
        if (!row.hidden) visible += 1;
        body.appendChild(row);
      });
      count.textContent = `${visible} of ${rows.length} result${rows.length === 1 ? "" : "s"}`;
      empty.hidden = visible !== 0;
    };

    search.addEventListener("input", updateResults);
    status.addEventListener("change", updateResults);
    sort.addEventListener("change", updateResults);
    body.addEventListener("change", (event) => {
      if (event.target.matches("select[name='status']")) updateResults();
    });
    panel.addEventListener("repeat-results-refresh", updateResults);
    updateResults();
  });

  document.querySelectorAll("[data-question-answer-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type='submit']");
      const saveStatus = form.querySelector("[data-answer-status]");
      button.disabled = true;
      saveStatus.textContent = " Saving…";
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || `HTTP ${response.status}`);
        }
        form.remove();
        const container = document.querySelector("[data-open-questions]");
        if (container && !container.querySelector("[data-question-answer-form]")) {
          container.remove();
        }
      } catch (error) {
        button.disabled = false;
        saveStatus.textContent = ` ${error.message}`;
      } finally {
        actionStarted = false;
      }
    });
  });
})();
