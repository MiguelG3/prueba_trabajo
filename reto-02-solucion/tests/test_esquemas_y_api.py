import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from src.config import Settings
from src.llm.adapter import HerramientaLLM, a_formato_openai, crear_adapter
from src.tools.contratos import HERRAMIENTAS
from tests.conftest import ScriptedAdapter, tool_call


def _schema(nombre: str, simple: bool = False) -> dict:
    t = HERRAMIENTAS[nombre]
    return a_formato_openai(HerramientaLLM(t.nombre, t.description, t.json_schema()), simple)["function"]["parameters"]


def test_literal_expone_enum_y_descripciones_afinadas():
    contrato = _schema("contratos_registrar")["properties"]["contrato"]
    opciones = next(o for o in contrato["anyOf"] if o.get("type") == "object")["properties"]
    assert next(o for o in opciones["moneda"]["anyOf"] if "enum" in o)["enum"] == ["COP", "USD", "PEN", "PAB", "HNL"]
    assert "Nunca estimes" in opciones["valor"]["description"]
    assert "Nunca lo pongas en true" in _schema("contratos_registrar")["properties"]["confirmado"]["description"]
    assert "$ref" not in json.dumps(_schema("contratos_registrar"))


def test_schema_simple_para_gemini_y_ollama():
    simple = json.dumps(_schema("contratos_registrar", simple=True))
    for clave in ('"anyOf"', '"pattern"', '"additionalProperties"', '"title"'):
        assert clave not in simple
    assert '"enum": ["COP", "USD", "PEN", "PAB", "HNL"]' in simple


def test_todas_las_descripciones_de_herramientas_son_una_frase():
    for t in HERRAMIENTAS.values():
        assert t.nombre.startswith("contratos_") and t.description.endswith(".") and t.description.count(". ") == 0


@pytest.mark.parametrize("proveedor", ["gemini", "groq", "openrouter", "ollama", "openai", "anthropic"])
def test_cada_proveedor_acepta_los_esquemas(proveedor):
    adapter = crear_adapter(Settings(_env_file=None, llm_provider=proveedor, llm_api_key="clave-falsa"))
    herramientas = [HerramientaLLM(t.nombre, t.description, t.json_schema()) for t in HERRAMIENTAS.values()]
    formateadas = [a_formato_openai(h, adapter._schema_simple) for h in herramientas]
    adapter._chat.bind_tools(formateadas)  # convierte al formato nativo del proveedor sin llamar a la red


def test_gemini_no_vuelve_obligatorios_los_campos_opcionales():
    from langchain_google_genai._function_utils import convert_to_genai_function_declarations

    declaracion = convert_to_genai_function_declarations([{"type": "function", "function": {
        "name": "contratos_registrar", "description": "x", "parameters": _schema("contratos_registrar", simple=True)}}])
    parametros = declaracion[0].function_declarations[0].parameters
    assert parametros.required == ["mensaje_id"]
    assert not parametros.properties["contrato"].required
    assert list(parametros.properties["contrato"].properties["moneda"].enum) == ["COP", "USD", "PEN", "PAB", "HNL"]


def test_openai_compatible_exige_base_url():
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        Settings(_env_file=None, llm_provider="openai_compatible", llm_model="x")


@pytest.fixture
def cliente(proyecto, monkeypatch):
    from src import server
    from src.graph import Agente

    adapter = ScriptedAdapter([tool_call("contratos_leer_buzon", {}, "c1"), AIMessage("Hay 6 mensajes pendientes.")])
    settings = Settings(_env_file=None, llm_provider="ollama", llm_api_key="secreta-123")
    monkeypatch.setattr(server, "_agente", Agente(adapter, settings, proyecto))
    monkeypatch.setattr(server, "RAIZ_PROYECTO", proyecto)
    return TestClient(server.app)


def test_api_chat_health_y_sesion(cliente):
    salud = cliente.get("/api/health").json()
    assert salud["ok"] and "secreta-123" not in json.dumps(salud)
    r = cliente.post("/api/chat", json={"sessionId": "abc", "message": "¿qué hay en el buzón?"}).json()
    assert r["reply"] == "Hay 6 mensajes pendientes."
    assert r["toolCalls"][0]["nombre"] == "contratos_leer_buzon" and r["toolCalls"][0]["ok"]
    assert r["needsConfirmation"] is False
    assert [m["rol"] for m in cliente.get("/api/sessions/abc").json()["mensajes"]] == ["usuario", "agente", "herramienta", "agente"]


def test_api_valida_entrada_y_bloquea_rutas(cliente):
    assert cliente.post("/api/chat", json={"sessionId": "../x", "message": "hola"}).status_code == 422
    assert cliente.post("/api/chat", json={"sessionId": "abc", "message": ""}).status_code == 422
    assert cliente.get("/api/out/../fixtures/reto-02/comerciales.json").status_code in (403, 404)
    assert cliente.get("/api/out/alertas.md").status_code == 404
    assert cliente.get("/").status_code == 200


def test_api_clave_de_acceso(cliente, monkeypatch):
    from src import server

    monkeypatch.setattr(server, "get_settings", lambda: Settings(_env_file=None, app_access_key="demo"))
    assert cliente.post("/api/chat", json={"sessionId": "a", "message": "x"}).status_code == 401
    ok = cliente.post("/api/chat", json={"sessionId": "a", "message": "x"}, headers={"X-Access-Key": "demo"})
    assert ok.status_code == 200
