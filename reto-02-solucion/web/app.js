const $ = (sel) => document.querySelector(sel);
const chat = $("#chat");
const entrada = $("#entrada");
const banner = $("#banner");
let sessionId = localStorage.getItem("sessionId") || nuevaSesionId();
let salud = {};
$("#clave").value = localStorage.getItem("clave") || "";

function nuevaSesionId() {
  const id = crypto.randomUUID();
  localStorage.setItem("sessionId", id);
  return id;
}

function headers() {
  const h = { "Content-Type": "application/json" };
  const clave = $("#clave").value.trim();
  if (clave) h["X-Access-Key"] = clave;
  localStorage.setItem("clave", clave);
  return h;
}

function markdown(texto) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(texto || ""));
  const div = document.createElement("div");
  div.textContent = texto || "";
  return div.innerHTML.replace(/\n/g, "<br>");
}

function agregar(rol, html, clase = "") {
  const burbuja = document.createElement("div");
  burbuja.className = `msg ${rol} ${clase}`;
  burbuja.innerHTML = html;
  chat.appendChild(burbuja);
  chat.scrollTop = chat.scrollHeight;
  return burbuja;
}

function escapar(texto) {
  const div = document.createElement("div");
  div.textContent = texto;
  return div.innerHTML;
}

function tarjetaHerramienta(llamada) {
  const estado = llamada.ok ? "ok" : "error";
  return `<details class="tool ${estado}">
    <summary><span class="tool-nombre">🔧 ${escapar(llamada.nombre)}</span>
      <span class="tool-badge ${estado}">${llamada.ok ? "ok" : "error"}</span>
      <span class="tool-resumen">${escapar(llamada.resumen || "")}</span></summary>
    <div class="tool-detalle">
      <h4>Argumentos</h4><pre>${escapar(JSON.stringify(llamada.args, null, 2))}</pre>
      <h4>Resultado</h4><pre>${escapar(JSON.stringify(llamada.resultado, null, 2))}</pre>
    </div>
  </details>`;
}

function mostrarBanner(pendientes) {
  if (!pendientes || pendientes.length === 0) {
    banner.classList.add("oculto");
    return;
  }
  banner.innerHTML = pendientes.map((p) => `
    <div class="pendiente">
      <strong>⏸ Confirmación requerida · ${escapar(p.mensaje_id)}</strong>
      <ul>${p.detalle.map((d) => `<li><code>${escapar(d.campo)}</code> = <b>${escapar(String(d.valor))}</b>
        <small>(confianza ${d.confianza}) — ${escapar(d.motivo)}</small></li>`).join("")}</ul>
      <button data-accion="confirmar" data-id="${escapar(p.mensaje_id)}">Confirmar valores</button>
      <button data-accion="rechazar" data-id="${escapar(p.mensaje_id)}" class="sec">No confirmar</button>
    </div>`).join("");
  banner.classList.remove("oculto");
  banner.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const p = pendientes.find((x) => x.mensaje_id === b.dataset.id);
    entrada.value = b.dataset.accion === "confirmar"
      ? `Confirmo para ${p.mensaje_id}: ${p.detalle.map((d) => `${d.campo} = ${d.valor}`).join(", ")}.`
      : `No confirmo ${p.mensaje_id}; déjalo pendiente sin registrar.`;
    entrada.focus();
  }));
}

async function enviar(texto) {
  agregar("usuario", escapar(texto));
  const pensando = agregar("agente", '<span class="puntos">Pensando</span>', "pensando");
  $("#enviar").disabled = true;
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: headers(), body: JSON.stringify({ sessionId, message: texto }) });
    const data = await r.json();
    pensando.remove();
    if (!r.ok) {
      agregar("agente", `⚠️ ${escapar(data.detail || "Error del servidor")}`, "error");
      return;
    }
    if (data.toolCalls.length) agregar("herramientas", data.toolCalls.map(tarjetaHerramienta).join(""));
    agregar("agente", markdown(data.reply || "_(sin respuesta)_"), data.needsConfirmation ? "confirmacion" : "");
    pintarModelo(data.modelo);
    mostrarBanner(data.pendientes);
  } catch (e) {
    pensando.remove();
    agregar("agente", "⚠️ No hay conexión con el servidor. Intenta de nuevo.", "error");
  } finally {
    $("#enviar").disabled = false;
  }
}

$("#form").addEventListener("submit", (e) => {
  e.preventDefault();
  const texto = entrada.value.trim();
  if (!texto) return;
  entrada.value = "";
  enviar(texto);
});
entrada.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#form").requestSubmit();
  }
});
document.querySelectorAll(".ejemplo").forEach((b) => b.addEventListener("click", () => {
  entrada.value = b.dataset.texto;
  entrada.focus();
}));
document.querySelectorAll(".archivos a").forEach((a) => a.addEventListener("click", async (e) => {
  e.preventDefault();
  const r = await fetch(`/api/out/${a.dataset.ruta}`, { headers: headers() });
  $("#visor-titulo").textContent = `out/${a.dataset.ruta}`;
  $("#visor-contenido").textContent = r.ok ? await r.text() : `(${r.status}) ${(await r.json()).detail}`;
  $("#visor").showModal();
}));
$("#visor-cerrar").addEventListener("click", () => $("#visor").close());
$("#btn-nueva").addEventListener("click", () => {
  sessionId = nuevaSesionId();
  chat.innerHTML = "";
  mostrarBanner([]);
});
$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("¿Borrar out/ (maestro, historial, alertas) y todas las sesiones?")) return;
  await fetch("/api/reset", { method: "POST", headers: headers() });
  $("#btn-nueva").click();
  agregar("agente", "Demo reiniciada: el buzón vuelve a tener los 6 mensajes pendientes.");
});

function pintarModelo(modeloQueRespondio) {
  const modelo = modeloQueRespondio || salud.model;
  $("#modelo").textContent = `${salud.provider} · ${modelo} · ${salud.tier}`;
  $("#modelo").title = modeloQueRespondio
    ? `Modelo que respondió el último mensaje (configurado: ${salud.model})`
    : "Modelo configurado en el servidor (LLM_MODEL)";
}

fetch("/api/health").then((r) => r.json()).then((h) => {
  salud = h;
  pintarModelo();
  $("#modelo").classList.add(h.ok ? "ok" : "error");
  if (!h.ok) agregar("agente", `⚠️ Modelo no configurado: ${escapar(h.error || "")}`, "error");
});
