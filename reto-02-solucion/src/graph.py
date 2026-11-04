"""Ciclo del agente con LangGraph: agente ⇄ herramientas, con tope de iteraciones y guard de confirmación.

    START → agente ──(tool_calls)──▶ herramientas ──▶ agente … ──(sin tool_calls)──▶ END

- CA1: al llegar a MAX_ITERACIONES el agente se invoca SIN herramientas y responde con lo que tiene.
- CA3: `contratos_registrar(confirmado=true)` solo se ejecuta si los campos en revisión de ese mensaje se
  presentaron en un turno ANTERIOR al mensaje actual del usuario. El modelo no puede autoconfirmarse.
- CA5: un error del proveedor se convierte en un mensaje claro; la sesión sigue viva.
"""

import json
import logging
import operator
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from src.config import Settings
from src.dominio.maestro import agregar_jsonl, ahora_iso
from src.llm.adapter import HerramientaLLM, LLMAdapter, texto_de
from src.tools.base import Contexto, Tool
from src.tools.contratos import HERRAMIENTAS

logger = logging.getLogger("agente")

NOMBRE_REGISTRAR = "contratos_registrar"
NOMBRE_VALIDAR = "contratos_validar"
AVISO_TOPE = (
    "[sistema] Se alcanzó el tope de {n} iteraciones de herramientas en este turno. Sin llamar más herramientas, "
    "resume lo que ya quedó hecho (solo con datos de los resultados de herramientas) y lo que falta por hacer."
)


class EstadoAgente(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    turno: Annotated[int, operator.add]  # cada mensaje del usuario suma 1
    iteraciones: int  # se reinicia en cada turno
    tokens_sesion: int
    pendientes: dict[str, dict]  # mensaje_id -> {campos, detalle, turno}


@dataclass
class ResultadoTurno:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    needs_confirmation: bool = False
    pendientes: list[dict] = field(default_factory=list)
    tokens_sesion: int = 0
    modelo: str | None = None  # el que reporta el proveedor en la respuesta (no el configurado)


def cargar_system_prompt(raiz: Path) -> str:
    """Comportamiento (agent/prompt.md) + conocimiento del proceso (src/knowledge/)."""
    prompt = (raiz / "agent" / "prompt.md").read_text(encoding="utf-8")
    conocimiento = (raiz / "src" / "knowledge" / "registro-contratos.md").read_text(encoding="utf-8")
    return f"{prompt}\n\n---\n\n# Conocimiento del proceso\n\n{conocimiento}"


def _codigo_http(exc: Exception) -> int | None:
    for candidato in (getattr(exc, "status_code", None), getattr(exc, "code", None),
                      getattr(getattr(exc, "response", None), "status_code", None)):
        if isinstance(candidato, int):
            return candidato
    return None


def mensaje_error_llm(exc: Exception, timeout_s: float) -> str:
    """Traduce el error del proveedor a lenguaje claro, con el código HTTP y un resumen del mensaje original."""
    clases = " ".join(c.__name__ for c in type(exc).__mro__)  # p. ej. GoogleAPIError → ServerError, ModelAPIError
    codigo = _codigo_http(exc)
    detalle = " ".join(str(exc).split())[:220]
    sufijo = f" [{codigo or type(exc).__name__}: {detalle}]" if detalle else f" [{codigo or type(exc).__name__}]"
    if "Timeout" in clases:
        return f"El modelo no respondió en {timeout_s:.0f} s. Intenta de nuevo en un momento."
    if codigo == 429 or "RateLimit" in clases or "ResourceExhausted" in clases:
        return "El proveedor del modelo limitó las solicitudes (cuota del plan gratuito). Espera un minuto e intenta de nuevo." + sufijo
    if codigo in (401, 403) or "Authentication" in clases or "PermissionDenied" in clases:
        return "El proveedor rechazó la llave del modelo (LLM_API_KEY inválida o sin permisos)." + sufijo
    if codigo == 404 or "NotFound" in clases:
        return "El modelo configurado (LLM_MODEL) no existe en el proveedor." + sufijo
    if (codigo and codigo >= 500) or "ServerError" in clases or "InternalServer" in clases:
        return ("El proveedor del modelo tiene una falla temporal o está saturado (no es un error de la solicitud). "
                "Intenta de nuevo en unos segundos; si persiste, cambia LLM_MODEL a otro modelo." + sufijo)
    if "Connection" in clases:
        return "No hay conexión con el proveedor del modelo." + sufijo
    return "Error del proveedor del modelo. La sesión sigue activa: intenta de nuevo." + sufijo


class Agente:
    def __init__(self, adapter: LLMAdapter, settings: Settings, raiz: Path, herramientas: dict[str, Tool] | None = None):
        self.adapter = adapter
        self.settings = settings
        self.raiz = raiz
        self.herramientas = herramientas or HERRAMIENTAS
        self.system_prompt = cargar_system_prompt(raiz)
        self._herramientas_llm = [HerramientaLLM(t.nombre, t.description, t.json_schema()) for t in self.herramientas.values()]
        self._tokens_globales = 0
        self._lock_tokens = threading.Lock()
        self.grafo = self._construir()

    # ---------- grafo ----------

    def _construir(self):
        grafo = StateGraph(EstadoAgente)
        grafo.add_node("agente", self._nodo_agente)
        grafo.add_node("herramientas", self._nodo_herramientas)
        grafo.add_edge(START, "agente")
        grafo.add_conditional_edges("agente", self._siguiente, {"herramientas": "herramientas", END: END})
        grafo.add_edge("herramientas", "agente")
        return grafo.compile(checkpointer=MemorySaver())

    @staticmethod
    def _siguiente(estado: EstadoAgente) -> str:
        ultimo = estado["messages"][-1]
        return "herramientas" if isinstance(ultimo, AIMessage) and ultimo.tool_calls else END

    def _nodo_agente(self, estado: EstadoAgente) -> dict:
        tokens = estado.get("tokens_sesion", 0)
        if tokens >= self.settings.max_tokens_sesion:
            return {"messages": [AIMessage(f"Se alcanzó el tope de tokens de esta sesión ({self.settings.max_tokens_sesion:,}). Abre una sesión nueva para continuar.")]}
        if self._tokens_globales >= self.settings.max_tokens_global:
            return {"messages": [AIMessage("Se alcanzó el tope global de uso del modelo en este servidor. Inténtalo más tarde.")]}

        nuevos: list[BaseMessage] = []
        herramientas = self._herramientas_llm
        if estado.get("iteraciones", 0) >= self.settings.max_iteraciones:
            nuevos.append(HumanMessage(AVISO_TOPE.format(n=self.settings.max_iteraciones)))
            herramientas = []
        try:
            respuesta = self.adapter.enviar([SystemMessage(self.system_prompt), *estado["messages"], *nuevos], herramientas)
        except Exception as exc:  # noqa: BLE001 — CA5: el error del proveedor no tumba la sesión
            logger.warning("Error del proveedor LLM (%s %s): %s", self.adapter.proveedor, self.adapter.modelo, " ".join(str(exc).split())[:1000])
            return {"messages": [*nuevos, AIMessage(f"⚠️ {mensaje_error_llm(exc, self.settings.llm_timeout_s)}")]}
        with self._lock_tokens:
            self._tokens_globales += respuesta.tokens
        mensaje = respuesta.mensaje
        if not herramientas and mensaje.tool_calls:
            # Sin herramientas disponibles no puede quedar un tool_call colgando (rompería el historial).
            mensaje = AIMessage(texto_de(mensaje) or "Llegué al tope de iteraciones de este turno; pídeme continuar.")
        return {"messages": [*nuevos, mensaje], "tokens_sesion": tokens + respuesta.tokens}

    def _nodo_herramientas(self, estado: EstadoAgente, config: RunnableConfig) -> dict:
        session_id = config["configurable"]["thread_id"]
        ctx = Contexto(directory=self.raiz, session_id=session_id, hoy=self.settings.fecha_referencia or date.today())
        pendientes = dict(estado.get("pendientes") or {})
        turno = estado.get("turno", 1)
        salidas = []
        for llamada in estado["messages"][-1].tool_calls:
            contenido = self._ejecutar(llamada, ctx, pendientes, turno)
            salidas.append(ToolMessage(content=contenido, tool_call_id=llamada["id"], name=llamada["name"]))
        return {"messages": salidas, "pendientes": pendientes, "iteraciones": estado.get("iteraciones", 0) + 1}

    # ---------- ejecución con guard ----------

    def _ejecutar(self, llamada: dict, ctx: Contexto, pendientes: dict[str, dict], turno: int) -> str:
        nombre, args = llamada["name"], llamada.get("args") or {}
        herramienta = self.herramientas.get(nombre)
        if herramienta is None:
            return json.dumps({"ok": False, "error": f"herramienta desconocida '{nombre}'. Disponibles: {', '.join(self.herramientas)}"})
        bloqueo = self._guard_confirmacion(nombre, args, pendientes, turno)
        if bloqueo:
            agregar_jsonl(ctx.rutas.log, {"ts": ahora_iso(), "herramienta": nombre, "mensaje_id": args.get("mensaje_id"),
                                          "ok": False, "resumen": f"guard: {bloqueo}", "session_id": ctx.session_id})
            return json.dumps({"ok": False, "error": bloqueo}, ensure_ascii=False)
        resultado = herramienta.execute(args, ctx)
        self._actualizar_pendientes(nombre, args, json.loads(resultado), pendientes, turno)
        return resultado

    @staticmethod
    def _guard_confirmacion(nombre: str, args: dict, pendientes: dict[str, dict], turno: int) -> str | None:
        if nombre != NOMBRE_REGISTRAR or not args.get("confirmado"):
            return None
        mensaje_id = args.get("mensaje_id")
        pendiente = pendientes.get(mensaje_id)
        if not pendiente:
            return (f"no hay campos en revisión presentados al usuario para {mensaje_id}. Ejecuta contratos_validar, "
                    "muestra los campos en revisión y pregunta antes de usar confirmado=true")
        if pendiente["turno"] >= turno:
            return ("requiere confirmación explícita del usuario: muestra los campos en revisión, termina el turno con una "
                    "pregunta y usa confirmado=true solo después de que el usuario responda")
        no_revisados = sorted(set((args.get("contrato") or {})) - set(pendiente["campos"]))
        if no_revisados:
            return (f"los campos {', '.join(no_revisados)} no fueron presentados al usuario para revisión; valida la "
                    "corrección con contratos_validar y vuelve a preguntar")
        return None

    @staticmethod
    def _actualizar_pendientes(nombre: str, args: dict, resultado: dict, pendientes: dict[str, dict], turno: int) -> None:
        mensaje_id = args.get("mensaje_id")
        if not mensaje_id:
            return
        if nombre == NOMBRE_VALIDAR and resultado.get("ok"):
            data = resultado["data"]
            if data["requiere_revision"]:
                pendientes[mensaje_id] = {"campos": data["requiere_revision"], "detalle": data["detalle_revision"], "turno": turno}
            else:
                pendientes.pop(mensaje_id, None)
        elif nombre == NOMBRE_REGISTRAR:
            detalle = (resultado.get("detalle") or {}).get("detalle_revision")
            if resultado.get("ok"):
                pendientes.pop(mensaje_id, None)
            elif detalle and mensaje_id not in pendientes:
                pendientes[mensaje_id] = {"campos": [d["campo"] for d in detalle], "detalle": detalle, "turno": turno}

    # ---------- API pública ----------

    def _config(self, session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": self.settings.max_iteraciones * 2 + 10}

    def conversar(self, session_id: str, mensaje: str) -> ResultadoTurno:
        config = self._config(session_id)
        previos = len(self.grafo.get_state(config).values.get("messages", []))
        estado = self.grafo.invoke({"messages": [HumanMessage(mensaje)], "turno": 1, "iteraciones": 0}, config)
        nuevos = estado["messages"][previos + 1:]
        respuesta = next((m for m in reversed(nuevos) if isinstance(m, AIMessage) and not m.tool_calls), None)
        pendientes = estado.get("pendientes") or {}
        return ResultadoTurno(
            reply=texto_de(respuesta) if respuesta else "",
            tool_calls=self._resumir_llamadas(nuevos),
            needs_confirmation=bool(pendientes),
            pendientes=[{"mensaje_id": k, "campos": v["campos"], "detalle": v["detalle"]} for k, v in pendientes.items()],
            tokens_sesion=estado.get("tokens_sesion", 0),
            modelo=self._modelo_que_respondio(nuevos),
        )

    def _modelo_que_respondio(self, mensajes: list[BaseMessage]) -> str:
        for m in reversed(mensajes):
            nombre = isinstance(m, AIMessage) and (m.response_metadata or {}).get("model_name")
            if nombre:
                return str(nombre).removeprefix("models/")
        return self.adapter.modelo

    def _resumir_llamadas(self, mensajes: list[BaseMessage]) -> list[dict]:
        resultados = {m.tool_call_id: m for m in mensajes if isinstance(m, ToolMessage)}
        llamadas = []
        for m in mensajes:
            if not isinstance(m, AIMessage):
                continue
            for llamada in m.tool_calls:
                salida = resultados.get(llamada["id"])
                cuerpo = json.loads(salida.content) if salida else {"ok": False, "error": "sin resultado"}
                herramienta = self.herramientas.get(llamada["name"])
                resumen = herramienta.resumir(cuerpo["data"]) if cuerpo.get("ok") and herramienta else cuerpo.get("error", "")
                llamadas.append({"nombre": llamada["name"], "args": llamada.get("args"), "ok": bool(cuerpo.get("ok")),
                                 "resumen": resumen, "resultado": cuerpo})
        return llamadas

    def historial(self, session_id: str) -> list[dict]:
        mensajes = self.grafo.get_state(self._config(session_id)).values.get("messages", [])
        historial = []
        for m in mensajes:
            if isinstance(m, HumanMessage):
                historial.append({"rol": "usuario", "contenido": texto_de(m)})
            elif isinstance(m, AIMessage):
                historial.append({"rol": "agente", "contenido": texto_de(m), "tool_calls": [
                    {"nombre": c["name"], "args": c.get("args")} for c in m.tool_calls]})
            elif isinstance(m, ToolMessage):
                historial.append({"rol": "herramienta", "nombre": m.name, "contenido": m.content})
        return historial
