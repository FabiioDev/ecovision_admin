// Panel EcoVision SV. Sin scripts en línea: la CSP solo permite 'self'.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  initConfirmDialogs();
  initConfigForm();
});

/* ------------------------------------------------------------------ *
 * Confirmación genérica: <form data-confirm="Mensaje"
 *   data-confirm-title="Título" data-confirm-ok="Botón" data-confirm-danger>
 * ------------------------------------------------------------------ */
function initConfirmDialogs() {
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog) return;
  let pending = null;

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (ev) => {
      if (form.dataset.confirmed === "1") return;
      ev.preventDefault();
      if (!form.reportValidity()) return;
      pending = form;
      const danger = form.hasAttribute("data-confirm-danger");
      dialog.querySelector("[data-title]").textContent = form.dataset.confirmTitle || "¿Confirmar acción?";
      dialog.querySelector("[data-message]").textContent = form.dataset.confirm;
      const ok = dialog.querySelector("[data-ok]");
      ok.textContent = form.dataset.confirmOk || "Confirmar";
      ok.className = "btn " + (danger ? "btn-danger" : "btn-primary");
      dialog.querySelector(".dialog-icon").classList.toggle("danger", danger);
      dialog.showModal();
    });
  });

  dialog.querySelector("[data-ok]").addEventListener("click", () => {
    dialog.close();
    if (pending) { pending.dataset.confirmed = "1"; pending.requestSubmit(); }
  });
  dialog.querySelector("[data-cancel]").addEventListener("click", () => dialog.close());
}

/* ------------------------------------------------------------------ *
 * Configuración: cambios pendientes, confirmación antes de publicar,
 * dependencias entre flags, deslizadores e índice lateral.
 * ------------------------------------------------------------------ */
function initConfigForm() {
  const form = document.getElementById("config-form");
  if (!form) return;
  const rows = [...form.querySelectorAll(".flag-row[data-key]")];
  const bar = document.getElementById("pending-bar");
  const publishDialog = document.getElementById("publish-dialog");
  let submitting = false;

  const control = (row) => row.querySelector("[data-field]");
  const current = (el) => (el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value.trim());
  const same = (el, a, b) =>
    el.type === "number" || el.type === "range" ? parseFloat(a) === parseFloat(b) : a === b;
  const display = (el, v) => {
    if (el.type === "checkbox") return v === "1" ? "Activado" : "Desactivado";
    if (v === "") return "(vacío)";
    return v.length > 40 ? v.slice(0, 40) + "…" : v;
  };

  function changes() {
    return rows
      .map((row) => {
        const el = control(row);
        const now = current(el);
        const was = el.dataset.published;
        return same(el, now, was) ? null : { row, el, label: row.dataset.label, was, now };
      })
      .filter(Boolean);
  }

  function refresh() {
    const list = changes();
    rows.forEach((row) => row.classList.remove("is-pending"));
    list.forEach((c) => c.row.classList.add("is-pending"));

    // dependencias (p. ej. history_enabled → history_save_enabled, export_enabled)
    rows.forEach((row) => {
      const dep = row.dataset.dependsOn;
      if (!dep) return;
      const parent = form.querySelector(`[data-field="${dep}"]`);
      row.classList.toggle("is-inactive", !!parent && !parent.checked);
    });

    // índice lateral: punto en los grupos con cambios
    document.querySelectorAll(".toc a[data-group]").forEach((a) => {
      const g = a.dataset.group;
      a.classList.toggle("has-pending", list.some((c) => c.row.closest(".config-group").id === g));
    });

    if (bar) {
      bar.classList.toggle("show", list.length > 0);
      bar.querySelector("[data-count]").textContent =
        list.length === 1 ? "1 cambio sin publicar" : `${list.length} cambios sin publicar`;
    }
  }

  // deslizadores con valor en InfoPill
  form.querySelectorAll(".range").forEach((wrap) => {
    const input = wrap.querySelector("input[type=range]");
    const out = wrap.querySelector("output");
    const show = () => (out.textContent = parseFloat(input.value).toFixed(2));
    input.addEventListener("input", show);
    show();
  });

  form.addEventListener("input", refresh);
  form.addEventListener("change", refresh);

  // Descartar: vuelve a los valores publicados
  document.querySelector("[data-discard]")?.addEventListener("click", () => {
    rows.forEach((row) => {
      const el = control(row);
      if (el.type === "checkbox") el.checked = el.dataset.published === "1";
      else el.value = el.dataset.published;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    refresh();
  });

  // Revisar y publicar: diálogo con la lista de cambios
  document.querySelector("[data-review]")?.addEventListener("click", () => {
    if (!form.reportValidity()) return;
    const list = changes();
    if (!list.length) return;
    const ul = publishDialog.querySelector("[data-changes]");
    ul.replaceChildren(
      ...list.map((c) => {
        const li = document.createElement("li");
        const k = document.createElement("span");
        k.className = "k";
        k.textContent = c.label;
        const v = document.createElement("span");
        v.className = "v";
        const del = document.createElement("del");
        del.textContent = display(c.el, c.was);
        const ins = document.createElement("ins");
        ins.textContent = display(c.el, c.now);
        v.append(del, "→", ins);
        li.append(k, v);
        return li;
      })
    );
    publishDialog.querySelector("[data-n]").textContent = list.length;
    publishDialog.showModal();
  });
  publishDialog?.querySelector("[data-cancel]").addEventListener("click", () => publishDialog.close());

  form.addEventListener("submit", () => (submitting = true));
  window.addEventListener("beforeunload", (ev) => {
    if (!submitting && changes().length) { ev.preventDefault(); ev.returnValue = ""; }
  });

  // índice: resaltar la sección visible
  const links = new Map([...document.querySelectorAll(".toc a[data-group]")].map((a) => [a.dataset.group, a]));
  if ("IntersectionObserver" in window && links.size) {
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => {
        if (e.isIntersecting) {
          links.forEach((a) => a.classList.remove("active"));
          links.get(e.target.id)?.classList.add("active");
        }
      }),
      { rootMargin: "-80px 0px -65% 0px" }
    );
    document.querySelectorAll(".config-group").forEach((s) => io.observe(s));
  }

  refresh();
}
