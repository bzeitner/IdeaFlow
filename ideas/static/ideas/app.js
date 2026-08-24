(() => {
  const parseDefaults = (raw = "") => Object.fromEntries(new URLSearchParams(raw));
  const managedParams = (source, names, defaults = {}) => {
    const managed = new URLSearchParams();
    names.forEach((name) => {
      source.getAll(name).filter(Boolean).forEach((value) => {
        if (value !== defaults[name]) managed.append(name, value);
      });
    });
    return managed;
  };
  const restoredParams = (current, saved) => {
    const restored = new URLSearchParams(current);
    restored.delete("page");
    new URLSearchParams(saved).forEach((value, name) => restored.append(name, value));
    return restored;
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { managedParams, parseDefaults, restoredParams };
  }
  if (typeof document === "undefined") return;

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

  const persistenceUser = document.body.dataset.persistenceUser;
  const persistenceKey = (scope) => `ideaflow.preferences.${persistenceUser}.${window.location.pathname}.${scope}`;

  if (persistenceUser) {
    const queryConfigs = Array.from(document.querySelectorAll("[data-persist-query-params]"));
    queryConfigs.forEach((config) => {
      const names = config.dataset.persistQueryParams.split(",").map((name) => name.trim());
      const defaults = parseDefaults(config.dataset.persistQueryDefaults);
      const key = persistenceKey("query");
      const savedParams = () => {
        const source = new URLSearchParams();
        names.forEach((name) => {
          config.querySelectorAll(`[name='${name}']`).forEach((control) => {
            if ((control.type === "checkbox" || control.type === "radio") && !control.checked) return;
            if (control.value) source.append(name, control.value);
          });
        });
        const params = managedParams(source, names, defaults);
        try {
          if (params.toString()) localStorage.setItem(key, params.toString());
          else localStorage.removeItem(key);
        } catch (_error) {
          // Filtering still works if local storage is unavailable.
        }
      };
      const urlParams = new URLSearchParams(window.location.search);
      const hasManagedQuery = names.some((name) => urlParams.has(name));
      if (hasManagedQuery) {
        const managed = managedParams(urlParams, names, defaults);
        try {
          if (managed.toString()) localStorage.setItem(key, managed.toString());
          else localStorage.removeItem(key);
        } catch (_error) {
          // Filtering still works if local storage is unavailable.
        }
      } else {
        try {
          const saved = localStorage.getItem(key);
          if (saved) {
            const restored = restoredParams(window.location.search, saved);
            window.location.replace(`${window.location.pathname}?${restored}`);
            return;
          }
        } catch (_error) {
          // Render the page defaults if local storage is unavailable.
        }
      }
      if (config.matches("form")) {
        config.addEventListener("submit", savedParams);
      }
    });

    document.querySelectorAll("[data-clear-persisted-query]").forEach((link) => {
      link.addEventListener("click", () => {
        try { localStorage.removeItem(persistenceKey("query")); } catch (_error) { /* Continue navigation. */ }
      });
    });

    document.querySelectorAll("[data-persist-controls]").forEach((container) => {
      const key = persistenceKey(`controls.${container.dataset.persistControls}`);
      const controls = Array.from(container.querySelectorAll("[data-persist-control]"));
      let saved = {};
      try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch (_error) { saved = {}; }
      const controlName = (control, index) => control.dataset.persistControl || control.name || control.id || String(index);
      controls.forEach((control, index) => {
        const name = controlName(control, index);
        if (Object.prototype.hasOwnProperty.call(saved, name)) {
          if (control.type === "checkbox" || control.type === "radio") control.checked = Boolean(saved[name]);
          else control.value = saved[name];
          control.dispatchEvent(new Event("input", { bubbles: true }));
          control.dispatchEvent(new Event("change", { bubbles: true }));
        }
        const saveControls = () => {
          const values = {};
          controls.forEach((item, itemIndex) => {
            values[controlName(item, itemIndex)] = item.type === "checkbox" || item.type === "radio" ? item.checked : item.value;
          });
          try { localStorage.setItem(key, JSON.stringify(values)); } catch (_error) { /* Controls still work. */ }
        };
        control.addEventListener("input", saveControls);
        control.addEventListener("change", saveControls);
      });
    });
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
