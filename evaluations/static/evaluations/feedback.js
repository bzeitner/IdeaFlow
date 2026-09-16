/* A GET/prefetch never records exposure. Observe only visible report content. */
(() => {
  document.querySelectorAll('.evaluation-feedback').forEach(panel => {
    const form = panel.querySelector('.feedback-form');
    if (!form) return;
    const output = document.getElementById(panel.dataset.observe);
    const message = panel.querySelector('.feedback-message');
    let visible = false, recorded = false, pending = false;
    const post = async data => {
      const response = await fetch(form.getAttribute('action'), {method: 'POST', body: data, credentials: 'same-origin', headers: {'Accept': 'application/json'}});
      if (!response.ok) throw new Error('Could not save. Check your input and reload if the output changed.');
      return response.json();
    };
    const expose = async () => {
      if (!visible || document.visibilityState !== 'visible' || recorded || pending) return;
      pending = true;
      const data = new FormData(form);
      data.set('operation', 'exposure'); data.set('visible', 'true');
      try { await post(data); recorded = true; } catch (_) { /* Remains unknown; another visibility event can retry. */ }
      finally { pending = false; }
    };
    if (output && 'IntersectionObserver' in window) {
      const observer = new IntersectionObserver(entries => {
        visible = entries.some(entry => entry.isIntersecting && entry.intersectionRect.height > 0 && entry.intersectionRect.width > 0);
        expose();
      });
      observer.observe(output);
      document.addEventListener('visibilitychange', expose);
    }
    panel.querySelectorAll('.feedback-form, .outcome-link-form').forEach(activeForm => activeForm.addEventListener('submit', async event => {
      event.preventDefault();
      const data = new FormData(activeForm);
      data.set('action', event.submitter?.value || '');
      try {
        await post(data);
        message.textContent = 'Saved. Reload to see the updated history.';
        activeForm.querySelectorAll('button').forEach(button => { button.disabled = true; });
      } catch (error) { message.textContent = error.message; }
    }));
  });
})();
