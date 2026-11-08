// Panel EcoVision: confirmaciones y aviso visual de dependencias entre flags.
// Sin scripts en línea (la CSP solo permite 'self').
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (ev) => {
      if (!window.confirm(form.dataset.confirm)) ev.preventDefault();
    });
  });

  const refresh = () => {
    document.querySelectorAll("[data-depends-on]").forEach((field) => {
      const parent = document.getElementById("f-" + field.dataset.dependsOn);
      field.classList.toggle("inactive", !!parent && !parent.checked);
    });
  };
  document.querySelectorAll("#config-form input[type=checkbox]").forEach((cb) =>
    cb.addEventListener("change", refresh));
  refresh();
});
