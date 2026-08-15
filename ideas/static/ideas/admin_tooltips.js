(() => {
  document.querySelectorAll(".form-row").forEach((row) => {
    const label = row.querySelector("label");
    if (!label) return;
    const help = row.querySelector(".help")?.textContent.trim();
    const description = help || `Edit ${label.textContent.trim().replace(/:$/, "").toLowerCase()}.`;
    label.title = description;
    row.querySelectorAll("input, select, textarea").forEach((field) => {
      if (!field.title) field.title = description;
    });
  });
  document.querySelectorAll("#content-main .module a, #nav-sidebar a").forEach((link) => {
    if (!link.title) link.title = `Open and manage ${link.textContent.trim()}.`;
  });
  document.querySelectorAll("th[scope='col']").forEach((heading) => {
    if (!heading.title) heading.title = `Review or sort by ${heading.textContent.trim()}.`;
  });
  document.querySelectorAll("button, input[type='submit'], a.button").forEach((control) => {
    if (!control.title) control.title = control.value || control.textContent.trim() || "Perform this action.";
  });
})();
