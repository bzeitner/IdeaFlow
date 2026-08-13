(() => {
  "use strict";
  const config = window.IDEAFLOW_GRAPH_LAB_CONFIG;
  const frame = document.getElementById("gephi-frame");
  const status = document.getElementById("bridge-status");
  let graphUrl = null;

  function report(type, values = {}) {
    window.parent.postMessage({ type, ...values }, config.ideaFlowOrigin);
  }

  window.addEventListener("message", async (event) => {
    if (event.origin !== config.ideaFlowOrigin || event.source !== window.parent) return;
    if (event.data?.type !== "ideaflow.graph.load") return;
    const { capability, export_url: exportUrl, graph_revision: revision } = event.data;
    if (typeof capability !== "string" || typeof exportUrl !== "string") return;
    let parsed;
    try { parsed = new URL(exportUrl); } catch (_error) { return; }
    if (parsed.origin !== config.ideaFlowOrigin || parsed.pathname !== "/graph-lab/export.graphml") return;
    try {
      status.textContent = "Loading IdeaFlow graph…";
      const response = await fetch(parsed.href, {
        headers: { Authorization: `GraphCapability ${capability}` },
        cache: "no-store",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `Graph export failed (${response.status}).`);
      }
      const blob = await response.blob();
      if (graphUrl) URL.revokeObjectURL(graphUrl);
      graphUrl = URL.createObjectURL(blob);
      frame.src = `${config.gephiPath}?file=${encodeURIComponent(graphUrl)}`;
      frame.hidden = false;
      status.hidden = true;
      report("ideaflow.graph.loaded", { revision });
    } catch (error) {
      status.hidden = false;
      status.textContent = error.message;
      report("ideaflow.graph.error", { message: error.message });
    }
  });
  window.addEventListener("pagehide", () => { if (graphUrl) URL.revokeObjectURL(graphUrl); });
  report("ideaflow.graph.ready");
})();
