(() => {
  const form = document.getElementById("graph-lab-controls");
  const frame = document.getElementById("graph-lab-frame");
  const status = document.getElementById("graph-lab-status");
  if (!form || !frame || !status) return;
  const graphLabOrigin = form.dataset.graphLabOrigin;
  let bridgeReady = false;

  async function loadGraph() {
    status.textContent = "Authorizing read-only access…";
    const response = await fetch(form.dataset.capabilityUrl, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      headers: { "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value },
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not authorize Graph Lab.");
    frame.contentWindow.postMessage({ type: "ideaflow.graph.load", ...payload }, graphLabOrigin);
    status.textContent = "Loading graph…";
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== graphLabOrigin || event.source !== frame.contentWindow) return;
    if (event.data?.type === "ideaflow.graph.ready") {
      bridgeReady = true;
      loadGraph().catch((error) => { status.textContent = error.message; });
    } else if (event.data?.type === "ideaflow.graph.loaded") {
      status.textContent = `Loaded revision ${event.data.revision}.`;
    } else if (event.data?.type === "ideaflow.graph.error") {
      status.textContent = event.data.message || "Graph Lab could not load the graph.";
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (bridgeReady) loadGraph().catch((error) => { status.textContent = error.message; });
  });
})();
