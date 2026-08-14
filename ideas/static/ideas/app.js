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
      select.disabled = true;
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
        savedValue = requestedValue;
        saveStatus.textContent = " Saved";
      } catch (_error) {
        select.value = savedValue;
        saveStatus.textContent = " Could not save";
      } finally {
        select.disabled = false;
        actionStarted = false;
      }
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
