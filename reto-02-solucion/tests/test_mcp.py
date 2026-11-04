import json
import socket
import sys
import threading
import time

import anyio
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from modulo import mcp_server
from src.config import Settings
from src.tools.contratos import HERRAMIENTAS
from tests.conftest import RAIZ

NOMBRES = {"leer_buzon", "extraer", "validar", "registrar", "alertas", "leer_pdf"}


@pytest.fixture
def servidor_aislado(proyecto, monkeypatch):
    monkeypatch.setattr(mcp_server, "RAIZ_PROYECTO", proyecto)
    return mcp_server.crear_servidor()


def _texto(resultado) -> dict:
    return json.loads(resultado.content[0].text)


def test_mcp_publica_las_mismas_herramientas_y_esquemas(servidor_aislado):
    async def probar():
        async with Client(servidor_aislado) as cliente:
            herramientas = {t.name: t for t in (await cliente.list_tools()).tools}
            assert set(herramientas) == NOMBRES
            assert herramientas["registrar"].input_schema == HERRAMIENTAS["contratos_registrar"].json_schema()
            assert herramientas["extraer"].annotations.read_only_hint is True
            assert herramientas["registrar"].annotations.read_only_hint is False
            assert "Las herramientas son tu única fuente de datos" in cliente.instructions

    anyio.run(probar)


def test_mcp_ejecuta_el_flujo_con_las_mismas_reglas(servidor_aislado, proyecto):
    async def probar():
        async with Client(servidor_aislado) as cliente:
            extraido = _texto(await cliente.call_tool("extraer", {"mensaje_id": "msg-001"}))
            assert extraido["data"]["id_contrato"]["valor"] == "CT-2026-015"
            registro = await cliente.call_tool("registrar", {"mensaje_id": "msg-001"})
            assert registro.is_error is False and _texto(registro)["data"]["accion"] == "insertado"
            pendiente = await cliente.call_tool("registrar", {"mensaje_id": "msg-006"})
            assert pendiente.is_error is True and _texto(pendiente)["error"] == "requiere revisión: valor, fecha_fin"
            invalido = await cliente.call_tool("extraer", {"mensaje_id": "../etc"})
            assert invalido.is_error and "argumentos inválidos" in _texto(invalido)["error"]

    anyio.run(probar)
    assert (proyecto / "out" / "sharepoint" / "maestro-contratos.csv").is_file()


def test_mcp_por_stdio_como_lo_lanza_un_cliente():
    parametros = StdioServerParameters(command=sys.executable, args=["-m", "modulo.mcp_server"], cwd=str(RAIZ))

    async def probar():
        async with Client(parametros) as cliente:
            assert {t.name for t in (await cliente.list_tools()).tools} == NOMBRES

    anyio.run(probar)


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor_http(proyecto, monkeypatch):
    """La app web real (FastAPI + /mcp) en uvicorn, como corre en Render."""
    from src import server

    monkeypatch.setattr(mcp_server, "RAIZ_PROYECTO", proyecto)
    monkeypatch.setattr(server, "get_settings", lambda: Settings(_env_file=None, app_access_key="clave-demo"))
    puerto = _puerto_libre()
    uvi = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=puerto, log_level="warning"))
    hilo = threading.Thread(target=uvi.run, daemon=True)
    hilo.start()
    while not uvi.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{puerto}/mcp"
    uvi.should_exit = True
    hilo.join(timeout=5)


def test_mcp_por_http_con_clave(servidor_http):
    async def probar():
        cabeceras = {"Authorization": "Bearer clave-demo"}
        async with httpx2.AsyncClient(headers=cabeceras) as http:
            async with Client(streamable_http_client(servidor_http, http_client=http)) as cliente:
                buzon = _texto(await cliente.call_tool("leer_buzon", {}))
                assert buzon["ok"] and buzon["data"]["pendientes"] == 6

    anyio.run(probar)


def test_mcp_por_http_sin_clave_responde_401(servidor_http):
    respuesta = httpx2.post(servidor_http, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert respuesta.status_code == 401
