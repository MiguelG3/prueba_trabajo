"""Servidor MCP con las MISMAS herramientas de la aplicación (src/tools/contratos.py).

Permite usar el agente desde cualquier plataforma compatible con MCP (OpenCode, Claude Code, Cursor...):

    Local (stdio):   uv run python -m modulo.mcp_server
    Remoto (HTTP):   la app web lo expone en /mcp  →  https://<servicio>.onrender.com/mcp

Las herramientas se publican con el nombre de su export (`leer_buzon`, `registrar`...). Con el servidor
registrado como "contratos", el cliente las muestra como `contratos_leer_buzon`, `contratos_registrar`...,
igual que el contrato `<archivo>_<export>` del PRD.
"""

import json
from datetime import date

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from starlette.applications import Starlette

from src.config import RAIZ_PROYECTO, get_settings
from src.graph import cargar_system_prompt
from src.tools.base import Contexto, Tool
from src.tools.contratos import HERRAMIENTAS

NOMBRE_SERVIDOR = "contratos"
PREFIJO = "contratos_"
SOLO_LECTURA = {"contratos_leer_buzon", "contratos_extraer", "contratos_validar", "contratos_leer_pdf"}
POR_NOMBRE_MCP: dict[str, Tool] = {t.nombre.removeprefix(PREFIJO): t for t in HERRAMIENTAS.values()}


def _definicion(nombre_mcp: str, herramienta: Tool) -> types.Tool:
    return types.Tool(
        name=nombre_mcp,
        description=herramienta.description,
        input_schema=herramienta.json_schema(),  # el mismo schema pydantic que ve el modelo en la app
        annotations=types.ToolAnnotations(
            read_only_hint=herramienta.nombre in SOLO_LECTURA,
            destructive_hint=False,  # registrar inserta o actualiza con historial; nunca borra
            idempotent_hint=True,  # registrar devuelve el resultado previo si el mensaje ya se procesó
            open_world_hint=False,
        ),
    )


async def _listar(_ctx, _params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[_definicion(n, t) for n, t in POR_NOMBRE_MCP.items()])


async def _llamar(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    herramienta = POR_NOMBRE_MCP.get(params.name)
    if herramienta is None:
        salida = json.dumps({"ok": False, "error": f"herramienta desconocida '{params.name}'"})
    else:
        settings = get_settings()
        ctx = Contexto(directory=RAIZ_PROYECTO, session_id="mcp", hoy=settings.fecha_referencia or date.today())
        # Las herramientas son síncronas (I/O de archivos): se ejecutan en un hilo para no bloquear el event loop.
        salida = await anyio.to_thread.run_sync(herramienta.execute, params.arguments or {}, ctx)
    cuerpo = json.loads(salida)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=salida)],
        structured_content=cuerpo,
        is_error=not cuerpo["ok"],
    )


def crear_servidor() -> Server:
    return Server(
        NOMBRE_SERVIDOR,
        version="1.0.0",
        instructions=cargar_system_prompt(RAIZ_PROYECTO),  # mismo prompt + conocimiento que usa la app
        on_list_tools=_listar,
        on_call_tool=_llamar,
    )


def crear_app_http() -> Starlette:
    """App ASGI del transporte Streamable HTTP. Sin estado: cada request es independiente (apto para Render)."""
    return crear_servidor().streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0",
    )


async def _main_stdio() -> None:
    servidor = crear_servidor()
    async with stdio_server() as (lectura, escritura):
        await servidor.run(lectura, escritura, servidor.create_initialization_options())


if __name__ == "__main__":
    anyio.run(_main_stdio)
