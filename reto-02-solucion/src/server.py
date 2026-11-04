"""API HTTP del agente + front estático. Arranque: `uv run dev`."""

import shutil
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.routing import Route

from src.config import RAIZ_PROYECTO, get_settings
from src.dominio.maestro import Rutas
from src.graph import Agente
from src.llm.adapter import crear_adapter


class McpHttp:
    """Endpoint ASGI /mcp: el servidor MCP del módulo, protegido con la misma APP_ACCESS_KEY del chat.

    Acepta la clave en `X-Access-Key` o `Authorization: Bearer <clave>` (lo que permita configurar el cliente MCP).
    El transporte se crea en el lifespan para que cada arranque tenga su propio session manager.
    """

    def __init__(self) -> None:
        self.app: Starlette | None = None

    @asynccontextmanager
    async def lifespan(self):
        from modulo.mcp_server import crear_app_http  # import perezoso: el chat no depende del SDK de MCP

        self.app = crear_app_http()
        async with self.app.router.lifespan_context(self.app):
            yield
        self.app = None

    async def __call__(self, scope, receive, send) -> None:
        clave = get_settings().app_access_key
        cabeceras = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        enviada = cabeceras.get("x-access-key") or cabeceras.get("authorization", "").removeprefix("Bearer ").strip()
        if clave and enviada != clave.get_secret_value():
            return await JSONResponse({"error": "Clave de acceso inválida (X-Access-Key o Authorization: Bearer)"}, 401)(scope, receive, send)
        if self.app is None:
            return await JSONResponse({"error": "El servidor MCP no está iniciado"}, 503)(scope, receive, send)
        await self.app(scope, receive, send)


mcp_http = McpHttp()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp_http.lifespan():
        yield


app = FastAPI(title="Agente de contratos vigentes — Periferia IT", version="1.0.0", lifespan=lifespan)
_locks_sesion: dict[str, threading.Lock] = defaultdict(threading.Lock)
_agente: Agente | None = None
_error_config: str | None = None


def _crear_agente() -> None:
    global _agente, _error_config
    try:
        _agente, _error_config = Agente(crear_adapter(get_settings()), get_settings(), RAIZ_PROYECTO), None
    except ValueError as exc:  # p. ej. falta LLM_API_KEY: el servidor arranca igual y lo explica
        _agente, _error_config = None, str(exc)


_crear_agente()

SessionId = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$", description="Identificador de la sesión de chat")


class ChatRequest(BaseModel):
    sessionId: str = SessionId
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    reply: str
    toolCalls: list[dict]
    needsConfirmation: bool
    pendientes: list[dict]
    tokensSesion: int
    modelo: str


def verificar_acceso(x_access_key: str | None = Header(None)) -> None:
    esperada = get_settings().app_access_key
    if esperada and x_access_key != esperada.get_secret_value():
        raise HTTPException(401, "Clave de acceso inválida (header X-Access-Key)")


def _agente_listo() -> Agente:
    if _agente is None:
        raise HTTPException(503, f"El modelo no está configurado: {_error_config}")
    return _agente


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    return {"ok": _agente is not None, "provider": s.llm_provider, "model": s.modelo, "tier": s.tier,
            "max_iteraciones": s.max_iteraciones, **({"error": _error_config} if _error_config else {})}


@app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(verificar_acceso)])
def chat(req: ChatRequest) -> ChatResponse:
    agente = _agente_listo()
    lock = _locks_sesion[req.sessionId]
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "La sesión ya está procesando un mensaje; espera la respuesta.")
    try:
        r = agente.conversar(req.sessionId, req.message)
    finally:
        lock.release()
    return ChatResponse(reply=r.reply, toolCalls=r.tool_calls, needsConfirmation=r.needs_confirmation,
                        pendientes=r.pendientes, tokensSesion=r.tokens_sesion, modelo=r.modelo or get_settings().modelo)


@app.get("/api/sessions/{session_id}", dependencies=[Depends(verificar_acceso)])
def sesion(session_id: str) -> dict:
    return {"sessionId": session_id, "mensajes": _agente_listo().historial(session_id)}


@app.post("/api/reset", dependencies=[Depends(verificar_acceso)])
def reset() -> dict:
    """Limpia out/ y las sesiones en memoria (útil para repetir la demo en la defensa)."""
    shutil.rmtree(Rutas(RAIZ_PROYECTO).out, ignore_errors=True)
    _crear_agente()
    return {"ok": True}


@app.get("/api/out/{ruta:path}", response_class=PlainTextResponse, dependencies=[Depends(verificar_acceso)])
def archivo_out(ruta: str) -> str:
    """Lectura de solo lectura de archivos generados en out/ (alertas.md, maestro, historial...)."""
    rutas = Rutas(RAIZ_PROYECTO)
    destino = (rutas.out / ruta).resolve()
    if not destino.is_relative_to(rutas.out.resolve()) or destino.suffix not in {".md", ".csv", ".json", ".jsonl", ".txt"}:
        raise HTTPException(403, "Ruta no permitida")
    if not destino.is_file():
        raise HTTPException(404, "Todavía no existe; procesa el buzón primero")
    return destino.read_text(encoding="utf-8")


app.router.routes.append(Route("/mcp", endpoint=mcp_http))  # antes del mount "/" del front
app.mount("/", StaticFiles(directory=Path(RAIZ_PROYECTO) / "web", html=True), name="web")


def main() -> None:
    uvicorn.run("src.server:app", host="0.0.0.0", port=get_settings().port)
