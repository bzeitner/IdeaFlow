(() => {
  const raw = JSON.parse(document.getElementById("graph-data").textContent);
  const elements = [
    ...raw.nodes.map((node) => ({ data: node })),
    ...raw.edges.map((edge) => ({ data: edge })),
  ];
  const cy = cytoscape({
    container: document.getElementById("knowledge-graph"), elements,
    minZoom: .2, maxZoom: 2.5,
    style: [
      { selector: "node", style: { "label": "data(label)", "background-color": "data(category_color)", "font-size": "9px", "font-weight": 600, "text-wrap": "wrap", "text-max-width": "105px", "text-valign": "bottom", "text-margin-y": "8px", "text-background-color": "#fff", "text-background-opacity": .85, "width": "38px", "height": "38px" } },
      { selector: "edge", style: { "label": "data(label)", "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": "#aab1bd", "target-arrow-color": "#aab1bd", "font-size": "8px", "text-rotation": "autorotate", "text-margin-y": "-8px", "text-background-color": "#fff", "text-background-opacity": .9 } },
      { selector: "edge[type = 'parent_of']", style: { "line-style": "dashed" } },
      { selector: ".faded", style: { "opacity": .12 } },
      { selector: ":selected", style: { "border-width": 4, "border-color": "#3b6fd4" } },
    ],
    layout: { name: "cose", animate: false, fit: true, padding: 25 },
  });
  const detail = document.getElementById("graph-detail");
  cy.on("tap", "node", (event) => {
    const d = event.target.data();
    detail.innerHTML = `<h3>${escapeHtml(d.label)}</h3><p>${escapeHtml(d.summary || "No summary.")}</p><p><span class="pill">${escapeHtml(d.status)}</span> <span class="pill">${escapeHtml(d.category)}</span></p><a class="btn small" href="${d.url}">Open idea</a>`;
  });
  cy.on("tap", "edge", (event) => {
    const d = event.target.data();
    detail.innerHTML = `<h3>${escapeHtml(d.label)}</h3><p>${escapeHtml(d.description || "No description.")}</p><p class="dim">Confidence ${d.confidence}/5 · ${escapeHtml(d.provenance)}</p>`;
  });
  document.getElementById("graph-fit").addEventListener("click", () => cy.fit(undefined, 25));
  document.getElementById("graph-layout").addEventListener("change", (event) => cy.layout({ name: event.target.value, animate: false, fit: true, padding: 25 }).run());
  function applyFilters() {
    const q = document.getElementById("graph-search").value.trim().toLowerCase();
    const status = document.getElementById("graph-status").value;
    const edgeType = document.getElementById("graph-edge-type").value;
    cy.elements().removeClass("faded");
    let nodes = cy.nodes().filter((node) => (!q || node.data("label").toLowerCase().includes(q)) && (!status || node.data("status") === status));
    let edges = edgeType ? cy.edges().filter((edge) => edge.data("type") === edgeType) : cy.edges();
    if (q || status || edgeType) cy.elements().difference(nodes.union(edges.filter((edge) => nodes.contains(edge.source()) || nodes.contains(edge.target())))).addClass("faded");
  }
  document.getElementById("graph-search").addEventListener("input", applyFilters);
  document.getElementById("graph-status").addEventListener("change", applyFilters);
  document.getElementById("graph-edge-type").addEventListener("change", applyFilters);
  const suggestionList = document.getElementById("suggestion-list");
  if (suggestionList) suggestionList.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-suggestion-review]");
    if (!form) return;
    event.preventDefault();
    const row = form.closest("[data-suggestion-row]");
    const buttons = row.querySelectorAll("button");
    const status = document.getElementById("suggestion-review-status");
    buttons.forEach((button) => { button.disabled = true; });
    status.textContent = "Saving review…";
    try {
      const response = await fetch(form.action, {
        method: "POST", body: new FormData(form), credentials: "same-origin",
        headers: { "Accept": "application/json", "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not review suggestion.");
      if (result.edge && cy.getElementById(result.edge.id).empty()) {
        const sourceExists = cy.getElementById(result.edge.source).nonempty();
        const targetExists = cy.getElementById(result.edge.target).nonempty();
        if (sourceExists && targetExists) cy.add({ data: result.edge });
      }
      row.remove();
      const remaining = suggestionList.querySelectorAll("[data-suggestion-row]").length;
      document.getElementById("suggestion-count").textContent = remaining;
      if (!remaining) suggestionList.innerHTML = '<tr id="suggestion-empty"><td colspan="6">No suggestions awaiting review.</td></tr>';
      status.textContent = result.message;
    } catch (error) {
      buttons.forEach((button) => { button.disabled = false; });
      status.textContent = error.message;
    }
  });
  function escapeHtml(value) { const div = document.createElement("div"); div.textContent = value == null ? "" : String(value); return div.innerHTML; }
})();
