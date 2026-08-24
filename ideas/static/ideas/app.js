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

  document.querySelectorAll("[data-auto-submit-filters]").forEach((form) => {
    let timer;
    let applying = false;
    const applyButton = form.querySelector("[data-filter-apply]") || form.querySelector("button[type='submit']");
    if (applyButton) applyButton.classList.add("js-filter-fallback");
    const resultStatus = form.querySelector("[data-filter-status]");
    const apply = () => {
      if (applying) return;
      applying = true;
      clearTimeout(timer);
      form.classList.add("is-applying");
      form.setAttribute("aria-busy", "true");
      if (resultStatus) resultStatus.textContent = "Updating results…";
      const page = form.querySelector("input[name='page']");
      if (page) page.value = "1";
      form.requestSubmit();
    };
    form.querySelectorAll("select, input[type='checkbox'], input[type='radio']").forEach((control) => {
      control.addEventListener("change", apply);
    });
    form.querySelectorAll("input[type='search']").forEach((search) => {
      search.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(apply, 400);
      });
      search.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && search.value) {
          search.value = "";
          apply();
        }
      });
    });
    form.addEventListener("submit", () => clearTimeout(timer));
  });

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

  document.querySelectorAll("[data-feed-rating-form]").forEach((form) => {
    const status = form.querySelector("[data-feed-rating-status]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitted = event.submitter;
      if (!submitted?.name) return;
      const buttons = Array.from(form.querySelectorAll("button[type='submit']"));
      const formData = new FormData(form);
      formData.set(submitted.name, submitted.value);
      buttons.forEach((button) => { button.disabled = true; });
      status.textContent = "Saving…";
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: formData,
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not save");
        form.querySelectorAll(`button[name='${payload.field}']`).forEach((button) => {
          const filled = Number(button.value) <= payload.value;
          button.classList.toggle("on", filled);
          button.textContent = filled ? "★" : "☆";
        });
        status.textContent = new URLSearchParams(window.location.search).has("unrated")
          ? "Saved — this item will leave this view when refreshed."
          : "Saved";
        window.setTimeout(() => {
          if (status.textContent === "Saved") status.textContent = "";
        }, 1800);
      } catch (error) {
        status.textContent = error.message;
      } finally {
        buttons.forEach((button) => { button.disabled = false; });
        actionStarted = false;
      }
    });
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

  document.querySelectorAll("[data-artifact-table]").forEach((table) => {
    const input = document.querySelector("[data-artifact-table-search]");
    const count = document.querySelector("[data-artifact-table-count]");
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    const update = () => {
      const query = (input?.value || "").trim().toLocaleLowerCase();
      let visible = 0;
      rows.forEach((row) => {
        row.hidden = Boolean(query) && !row.textContent.toLocaleLowerCase().includes(query);
        if (!row.hidden) visible += 1;
      });
      if (count) count.textContent = `${visible} of ${rows.length} rows`;
    };
    if (input) input.addEventListener("input", update);
    update();
  });

  document.querySelectorAll("[data-wrap-artifact]").forEach((toggle) => {
    const raw = document.querySelector("[data-artifact-raw]");
    toggle.addEventListener("change", () => raw?.classList.toggle("wrap", toggle.checked));
  });

  document.querySelectorAll("[data-auto-save]").forEach((form) => {
    const control = form.querySelector("input[name='value'], select[name='value']");
    const status = form.querySelector("[data-save-status]");
    const button = form.querySelector("button[type='submit']");
    let savedValue = control.value;
    let saving = false;
    button?.classList.add("js-save-fallback");

    const save = async () => {
      if (saving || control.value === savedValue) return;
      saving = true;
      const requestedValue = control.value;
      const formData = new FormData(form);
      control.disabled = true;
      status.textContent = "Saving…";
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: formData,
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not save");
        savedValue = requestedValue;
        status.textContent = "Saved";
        window.setTimeout(() => { if (status.textContent === "Saved") status.textContent = ""; }, 1800);
      } catch (error) {
        control.value = savedValue;
        status.textContent = error.message;
      } finally {
        control.disabled = false;
        saving = false;
        actionStarted = false;
      }
    };
    form.addEventListener("submit", (event) => { event.preventDefault(); save(); });
    if (control.matches("select")) control.addEventListener("change", save);
    else {
      control.addEventListener("blur", save);
      control.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); control.blur(); }
        if (event.key === "Escape") { control.value = savedValue; control.blur(); status.textContent = "Reverted"; }
      });
    }
  });

  document.querySelectorAll("form.idea-form").forEach((form) => {
    let dirty = false;
    form.addEventListener("input", () => { dirty = true; });
    form.addEventListener("change", () => { dirty = true; });
    form.addEventListener("submit", () => { dirty = false; });
    window.addEventListener("beforeunload", (event) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
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
