// Demo web EcoVision SV: captura, envío a /demo/predict y dibujo de detecciones.
// Sin scripts en línea (CSP 'self'). El texto del modelo se inserta siempre con
// textContent, nunca como HTML.
"use strict";

const MAX_LADO = 1280;      // la imagen se reduce antes de subirla (el modelo usa 640×640)
const CALIDAD_JPEG = 0.88;

const el = (id) => document.getElementById(id);
const demo = el("demo");
const CFG = JSON.parse(demo.dataset.cfg);

const marco = el("marco");
const vacio = el("vacio");
const video = el("video");
const lienzo = el("lienzo");
const ctx = lienzo.getContext("2d");
const cargando = el("cargando");
const aviso = el("aviso");
const inputArchivo = el("input-archivo");
const umbral = el("umbral");
const umbralValor = el("umbral-valor");
const btnArchivo = el("btn-archivo");
const btnCamara = el("btn-camara");
const btnCapturar = el("btn-capturar");
const btnCambiar = el("btn-cambiar");
const btnCerrarCamara = el("btn-cerrar-camara");

// Colores de detección de DESIGN.md (--det-1 … --det-8), en orden de aparición
const estilos = getComputedStyle(document.documentElement);
const PALETA = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => estilos.getPropertyValue(`--det-${i}`).trim() || "#26A641");
const colorDe = (i) => PALETA[i % PALETA.length];

// Consejos por categoría: se leen de la sección "Acerca de" (una sola fuente)
const normalizar = (t) => (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
const CONSEJOS = new Map(
  [...document.querySelectorAll("#categorias .categoria")].map((c) => [
    c.dataset.cat,
    { nombre: c.querySelector("strong").textContent, texto: c.querySelector("p").textContent },
  ])
);

let imagenActual = null;   // canvas con la imagen ya redimensionada
let blobActual = null;     // el mismo fotograma en JPEG
let respuesta = null;      // respuesta cruda del modelo (umbral bajo)
let visibles = [];         // detecciones tras umbral, NMS y máximo
let stream = null;
let camaraTrasera = true;
let analizando = false;

/* ---------------- estado del servicio ---------------- */
async function comprobarEstado() {
  const caja = el("estado");
  const texto = el("estado-texto");
  try {
    const r = await fetch("/demo/estado", { cache: "no-store" });
    const d = await r.json();
    if (d.conectado) {
      caja.dataset.estado = "ok";
      const n = (d.clases || []).length;
      texto.textContent = n ? `Modelo listo · ${n} categorías` : "Modelo listo";
    } else {
      caja.dataset.estado = "error";
      texto.textContent = d.disponible === false ? "No disponible" : "Modelo no disponible";
    }
  } catch {
    caja.dataset.estado = "error";
    texto.textContent = "Sin conexión";
  }
}

/* ---------------- utilidades ---------------- */
function mostrarAviso(mensaje) {
  aviso.textContent = mensaje || "";
  aviso.hidden = !mensaje;
}

function escalar(ancho, alto) {
  const f = Math.min(1, MAX_LADO / Math.max(ancho, alto));
  return [Math.round(ancho * f), Math.round(alto * f)];
}

const aBlob = (canvas) => new Promise((res) => canvas.toBlob(res, "image/jpeg", CALIDAD_JPEG));

async function prepararDesdeFuente(fuente, anchoOrigen, altoOrigen) {
  const [w, h] = escalar(anchoOrigen, altoOrigen);
  const tmp = document.createElement("canvas");
  tmp.width = w;
  tmp.height = h;
  tmp.getContext("2d").drawImage(fuente, 0, 0, w, h);
  const blob = await aBlob(tmp);
  if (!blob) throw new Error("No se pudo preparar la imagen.");
  if (blob.size > CFG.max_upload_mb * 1024 * 1024) throw new Error(`La imagen supera ${CFG.max_upload_mb} MB.`);
  blobActual = blob;
  imagenActual = tmp;
  respuesta = null;
  visibles = [];
  pintar();
}

function cargarArchivo(archivo) {
  if (!archivo) return;
  if (!/^image\/(jpeg|png|webp)$/.test(archivo.type)) {
    mostrarAviso("Ese archivo no es una imagen admitida. Usa JPEG, PNG o WebP.");
    return;
  }
  mostrarAviso("");
  const url = URL.createObjectURL(archivo);
  const img = new Image();
  img.onload = async () => {
    URL.revokeObjectURL(url);
    try {
      await prepararDesdeFuente(img, img.naturalWidth, img.naturalHeight);
      analizar();
    } catch (e) {
      mostrarAviso(e.message);
    }
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    mostrarAviso("No se pudo leer la imagen.");
  };
  img.src = url;
}

/* ---------------- cámara ---------------- */
function mostrarControlesCamara(abierta) {
  btnCapturar.hidden = !abierta;
  btnCambiar.hidden = !abierta;
  btnCerrarCamara.hidden = !abierta;
  btnCamara.hidden = abierta;
  btnArchivo.hidden = abierta;
}

async function abrirCamara() {
  mostrarAviso("");
  if (!navigator.mediaDevices?.getUserMedia) {
    // Sin acceso directo a la cámara: selector del sistema con captura
    inputArchivo.setAttribute("capture", "environment");
    inputArchivo.click();
    return;
  }
  try {
    detenerStream();
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: camaraTrasera ? { ideal: "environment" } : "user" },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    vacio.hidden = true;
    lienzo.hidden = true;
    video.hidden = false;
    marco.classList.add("con-imagen");
    mostrarControlesCamara(true);
  } catch {
    cerrarCamara();
    mostrarAviso("No se pudo abrir la cámara. Revisa los permisos del navegador; en el móvil la página debe abrirse por HTTPS.");
  }
}

function detenerStream() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
}

function cerrarCamara() {
  detenerStream();
  video.hidden = true;
  mostrarControlesCamara(false);
  pintar();
}

async function capturar() {
  if (!video.videoWidth) return;
  try {
    await prepararDesdeFuente(video, video.videoWidth, video.videoHeight);
    cerrarCamara();
    analizar();
  } catch (e) {
    mostrarAviso(e.message);
  }
}

/* ---------------- inferencia ---------------- */
async function analizar() {
  if (!blobActual || analizando) return;
  analizando = true;
  mostrarAviso("");
  cargando.hidden = false;
  btnArchivo.disabled = btnCamara.disabled = true;
  try {
    const cuerpo = new FormData();
    cuerpo.append("file", blobActual, "captura.jpg");
    const r = await fetch("/demo/predict", { method: "POST", body: cuerpo });
    let datos = {};
    try { datos = await r.json(); } catch { /* respuesta sin JSON */ }
    if (!r.ok) throw new Error(datos.detail || `Error ${r.status}`);
    respuesta = datos;
    pintar();
  } catch (e) {
    mostrarAviso(e.message || "Falló el análisis. Inténtalo de nuevo.");
  } finally {
    cargando.hidden = true;
    analizando = false;
    btnArchivo.disabled = btnCamara.disabled = false;
  }
}

/* ---------------- filtro: umbral, NMS y máximo (igual que la app) ---------------- */
function area(b) {
  return Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);
}

function iou(a, b) {
  const x1 = Math.max(a[0], b[0]), y1 = Math.max(a[1], b[1]);
  const x2 = Math.min(a[2], b[2]), y2 = Math.min(a[3], b[3]);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const union = area(a) + area(b) - inter;
  return union > 0 ? inter / union : 0;
}

function filtrar(detecciones) {
  const minimo = parseFloat(umbral.value);
  const ordenadas = detecciones
    .filter((d) => d.confianza >= minimo && Array.isArray(d.bbox_norm) && d.bbox_norm.length === 4)
    .sort((a, b) => b.confianza - a.confianza);
  if (!CFG.nms_enabled) return ordenadas.slice(0, CFG.max_detections);
  const quedan = [];
  for (const d of ordenadas) {
    // Misma regla que la app: se elimina si IoU > nms_iou, es decir, se conserva si
    // IoU <= nms_iou con todas las ya aceptadas (0 = elimina cualquier solapamiento)
    if (quedan.every((k) => iou(k.bbox_norm, d.bbox_norm) <= CFG.nms_iou)) quedan.push(d);
    if (quedan.length >= CFG.max_detections) break;
  }
  return quedan;
}

/* ---------------- dibujo ---------------- */
function pintar() {
  visibles = respuesta ? filtrar(respuesta.detecciones || []) : [];
  dibujar();
  pintarFicha();
}

function dibujar(resaltada = -1) {
  const hayImagen = !!imagenActual && video.hidden;
  vacio.hidden = hayImagen || !video.hidden;
  lienzo.hidden = !hayImagen;
  marco.classList.toggle("con-imagen", hayImagen || !video.hidden);
  if (!hayImagen) return;

  lienzo.width = imagenActual.width;
  lienzo.height = imagenActual.height;
  ctx.drawImage(imagenActual, 0, 0);

  const base = Math.max(lienzo.width, lienzo.height);
  const grosor = Math.max(2, Math.round(base * 0.004));
  const tipo = Math.max(13, Math.round(base * 0.024));
  ctx.font = `600 ${tipo}px "Be Vietnam Pro", system-ui, sans-serif`;
  ctx.textBaseline = "top";

  visibles.forEach((d, i) => {
    const [xmin, ymin, xmax, ymax] = d.bbox_norm;
    const x = xmin * lienzo.width;
    const y = ymin * lienzo.height;
    const w = (xmax - xmin) * lienzo.width;
    const h = (ymax - ymin) * lienzo.height;
    const color = colorDe(i);

    ctx.globalAlpha = resaltada === -1 || resaltada === i ? 1 : 0.25;
    ctx.strokeStyle = color;
    ctx.lineWidth = resaltada === i ? grosor * 1.8 : grosor;
    ctx.strokeRect(x, y, w, h);

    const etiqueta = `${i + 1} · ${d.categoria} ${(d.confianza * 100).toFixed(0)}%`;
    const anchoTxt = ctx.measureText(etiqueta).width + tipo * 0.7;
    const altoTxt = tipo * 1.5;
    const yTxt = y - altoTxt < 0 ? y : y - altoTxt;
    ctx.fillStyle = color;
    ctx.fillRect(x, yTxt, anchoTxt, altoTxt);
    ctx.fillStyle = "#FFFFFF";
    ctx.fillText(etiqueta, x + tipo * 0.35, yTxt + tipo * 0.22);
    ctx.globalAlpha = 1;
  });
}

function pintarFicha() {
  const lista = el("detecciones");
  const vaciaTxt = el("ficha-vacia");
  const contador = el("m-objetos");
  lista.replaceChildren();

  if (!respuesta) {
    contador.hidden = true;
    el("metricas").hidden = true;
    el("consejos").hidden = true;
    vaciaTxt.hidden = false;
    vaciaTxt.textContent = imagenActual ? "Aún no hay resultados para esta imagen." : "Todavía no hay nada que analizar.";
    return;
  }

  contador.hidden = false;
  contador.textContent = visibles.length === 1 ? "1 objeto" : `${visibles.length} objetos`;
  el("metricas").hidden = false;
  el("m-latencia").textContent = `${Math.round(respuesta.latencia_ms)} ms`;
  el("m-tam").textContent = `${respuesta.ancho}×${respuesta.alto}`;
  vaciaTxt.hidden = visibles.length > 0;
  vaciaTxt.textContent = "Sin detecciones por encima de la confianza mínima. Bájala o prueba otra foto.";

  visibles.forEach((d, i) => {
    const li = document.createElement("li");
    li.className = "deteccion";
    li.tabIndex = 0;
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.style.backgroundColor = colorDe(i);
    chip.textContent = String(i + 1);
    const nombre = document.createElement("span");
    nombre.className = "nombre";
    nombre.textContent = d.categoria;
    const conf = document.createElement("span");
    conf.className = "confianza";
    conf.textContent = `${(d.confianza * 100).toFixed(1)}%`;
    li.append(chip, nombre, conf);
    const resaltar = () => dibujar(i);
    const quitar = () => dibujar();
    li.addEventListener("mouseenter", resaltar);
    li.addEventListener("mouseleave", quitar);
    li.addEventListener("focus", resaltar);
    li.addEventListener("blur", quitar);
    lista.appendChild(li);
  });

  // Un consejo por categoría detectada
  const vistos = new Set();
  const consejos = [];
  visibles.forEach((d) => {
    const clave = normalizar(d.categoria);
    if (vistos.has(clave) || !CONSEJOS.has(clave)) return;
    vistos.add(clave);
    const c = CONSEJOS.get(clave);
    const div = document.createElement("div");
    div.className = "consejo";
    const t = document.createElement("strong");
    t.textContent = c.nombre;
    const p = document.createElement("p");
    p.textContent = c.texto;
    div.append(t, p);
    consejos.push(div);
  });
  el("consejos-lista").replaceChildren(...consejos);
  el("consejos").hidden = consejos.length === 0;
}

/* ---------------- eventos ---------------- */
btnArchivo.addEventListener("click", () => {
  inputArchivo.removeAttribute("capture");
  inputArchivo.click();
});
inputArchivo.addEventListener("change", (e) => {
  if (stream) cerrarCamara();
  cargarArchivo(e.target.files[0]);
  e.target.value = "";
});
btnCamara.addEventListener("click", abrirCamara);
btnCapturar.addEventListener("click", capturar);
btnCerrarCamara.addEventListener("click", cerrarCamara);
btnCambiar.addEventListener("click", () => {
  camaraTrasera = !camaraTrasera;
  abrirCamara();
});

// El deslizador filtra en el navegador: no vuelve a enviar la foto
umbral.addEventListener("input", () => {
  umbralValor.textContent = Number(umbral.value).toFixed(2);
  if (respuesta) pintar();
});

marco.addEventListener("dragover", (e) => {
  e.preventDefault();
  marco.classList.add("arrastrando");
});
marco.addEventListener("dragleave", () => marco.classList.remove("arrastrando"));
marco.addEventListener("drop", (e) => {
  e.preventDefault();
  marco.classList.remove("arrastrando");
  if (stream) cerrarCamara();
  cargarArchivo(e.dataTransfer.files[0]);
});

window.addEventListener("pagehide", detenerStream);
comprobarEstado();
pintarFicha();
