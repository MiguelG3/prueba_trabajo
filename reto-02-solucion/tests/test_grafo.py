import json

import pytest
from langchain_core.messages import AIMessage

from src.config import Settings
from src.graph import Agente
from tests.conftest import ScriptedAdapter, tool_call

CONFIRMAR_006 = {"mensaje_id": "msg-006", "contrato": {"valor": 0, "fecha_fin": "2027-08-31"}, "confirmado": True}


def _agente(proyecto, respuestas, **ajustes) -> tuple[Agente, ScriptedAdapter]:
    adapter = ScriptedAdapter(respuestas)
    settings = Settings(_env_file=None, llm_provider="ollama", fecha_referencia="2026-09-03", **ajustes)
    return Agente(adapter, settings, proyecto), adapter


def _resultado(turno, nombre):
    return next(c["resultado"] for c in turno.tool_calls if c["nombre"] == nombre)


def test_system_prompt_incluye_comportamiento_y_conocimiento(proyecto):
    agente, adapter = _agente(proyecto, [AIMessage("hola")])
    agente.conversar("s1", "hola")
    sistema = adapter.llamadas[0]["mensajes"][0].content
    assert "Las herramientas son tu única fuente de datos" in sistema and "Conocimiento del proceso" in sistema
    assert len(adapter.llamadas[0]["herramientas"]) == 6


def test_guard_impide_autoconfirmar_en_el_mismo_turno(proyecto):
    agente, _ = _agente(proyecto, [
        tool_call("contratos_validar", {"mensaje_id": "msg-006"}, "c1"),
        tool_call("contratos_registrar", CONFIRMAR_006, "c2"),  # el modelo intenta confirmarse solo
        AIMessage("¿Confirmas valor 0 y fecha fin 2027-08-31 para msg-006?"),
    ])
    turno = agente.conversar("s1", "procesa msg-006")
    bloqueo = _resultado(turno, "contratos_registrar")
    assert bloqueo["ok"] is False and "requiere confirmación explícita del usuario" in bloqueo["error"]
    assert turno.needs_confirmation and turno.pendientes[0]["campos"] == ["valor", "fecha_fin"]
    assert not (proyecto / "out" / "procesados.json").exists()


def test_confirmacion_en_el_turno_siguiente_registra(proyecto):
    agente, _ = _agente(proyecto, [
        tool_call("contratos_validar", {"mensaje_id": "msg-006"}, "c1"),
        AIMessage("¿Confirmas valor 0 y fecha fin para msg-006?"),
        tool_call("contratos_registrar", CONFIRMAR_006, "c2"),
        AIMessage("Registrado CM-2026-03."),
    ])
    agente.conversar("s1", "procesa msg-006")
    turno = agente.conversar("s1", "confirmo el valor 0 y la fecha fin 2027-08-31")
    registro = _resultado(turno, "contratos_registrar")
    assert registro["ok"] and registro["data"]["id_contrato"] == "CM-2026-03"
    assert turno.needs_confirmation is False and turno.reply == "Registrado CM-2026-03."


def test_guard_rechaza_correcciones_de_campos_no_revisados(proyecto):
    agente, _ = _agente(proyecto, [
        tool_call("contratos_validar", {"mensaje_id": "msg-006"}, "c1"),
        AIMessage("¿Confirmas?"),
        tool_call("contratos_registrar", {**CONFIRMAR_006, "contrato": {"valor": 0, "cliente": "Otro S.A."}}, "c2"),
        AIMessage("ok"),
    ])
    agente.conversar("s1", "procesa msg-006")
    turno = agente.conversar("s1", "sí")
    assert "cliente no fueron presentados" in _resultado(turno, "contratos_registrar")["error"]


def test_guard_sin_validacion_previa(proyecto):
    agente, _ = _agente(proyecto, [tool_call("contratos_registrar", CONFIRMAR_006, "c1"), AIMessage("ok")])
    turno = agente.conversar("s1", "registra msg-006 confirmado")
    assert "no hay campos en revisión presentados" in _resultado(turno, "contratos_registrar")["error"]


def test_tope_de_iteraciones_responde_sin_herramientas(proyecto):
    contador = iter(range(100))

    def modelo_insistente(_mensajes, herramientas):
        if herramientas:
            return tool_call("contratos_leer_buzon", {}, f"c{next(contador)}")
        return AIMessage("Hice 3 lecturas; falta registrar.")

    agente, adapter = _agente(proyecto, [modelo_insistente] * 10, max_iteraciones=3)
    turno = agente.conversar("s1", "lee el buzón para siempre")
    assert len(turno.tool_calls) == 3
    assert adapter.llamadas[-1]["herramientas"] == []
    assert "tope de 3 iteraciones" in adapter.llamadas[-1]["mensajes"][-1].content
    assert turno.reply == "Hice 3 lecturas; falta registrar."


@pytest.mark.parametrize("excepcion, texto", [
    (type("APITimeoutError", (Exception,), {})(), "no respondió"),
    (type("RateLimitError", (Exception,), {})(), "cuota del plan gratuito"),
    (type("AuthenticationError", (Exception,), {})(), "llave del modelo"),
])
def test_error_del_proveedor_no_mata_la_sesion(proyecto, excepcion, texto):
    agente, _ = _agente(proyecto, [excepcion, AIMessage("de vuelta")])
    assert texto in agente.conversar("s1", "hola").reply
    assert agente.conversar("s1", "¿sigues?").reply == "de vuelta"


def test_herramienta_desconocida_y_argumentos_invalidos(proyecto):
    agente, _ = _agente(proyecto, [
        AIMessage(content="", tool_calls=[
            {"name": "contratos_borrar_todo", "args": {}, "id": "c1", "type": "tool_call"},
            {"name": "contratos_extraer", "args": {"mensaje_id": 7}, "id": "c2", "type": "tool_call"},
        ]),
        AIMessage("no pude"),
    ])
    turno = agente.conversar("s1", "x")
    assert "herramienta desconocida" in turno.tool_calls[0]["resultado"]["error"]
    assert "argumentos inválidos" in turno.tool_calls[1]["resultado"]["error"]


def test_tope_de_tokens_por_sesion(proyecto):
    agente, adapter = _agente(proyecto, [AIMessage("uno")] * 20, max_tokens_sesion=1000)
    for _ in range(10):
        agente.conversar("s1", "hola")  # cada respuesta guionada consume 100 tokens
    assert "tope de tokens" in agente.conversar("s1", "hola").reply
    assert len(adapter.llamadas) == 10


def test_historial_de_sesion(proyecto):
    agente, _ = _agente(proyecto, [tool_call("contratos_leer_buzon", {}, "c1"), AIMessage("6 pendientes")])
    agente.conversar("s1", "¿qué hay?")
    roles = [m["rol"] for m in agente.historial("s1")]
    assert roles == ["usuario", "agente", "herramienta", "agente"]
    assert json.loads(agente.historial("s1")[2]["contenido"])["ok"] is True


def test_tool_calls_al_llegar_al_tope_se_descartan(proyecto):
    agente, _ = _agente(proyecto, [tool_call("contratos_leer_buzon", {}, f"c{i}") for i in range(5)], max_iteraciones=2)
    turno = agente.conversar("s1", "x")
    assert len(turno.tool_calls) == 2 and "tope de iteraciones" in turno.reply


def test_error_5xx_se_explica_con_codigo_y_detalle(proyecto):
    class ServerError(Exception):
        code = 503

    class GoogleAPIError(ServerError):
        pass

    agente, _ = _agente(proyecto, [GoogleAPIError("503 UNAVAILABLE. The model is overloaded. Please try again later.")])
    respuesta = agente.conversar("s1", "hola").reply
    assert "falla temporal o está saturado" in respuesta and "[503: 503 UNAVAILABLE. The model is overloaded" in respuesta


def test_reporta_el_modelo_que_respondio_no_el_configurado(proyecto):
    servido = AIMessage("hola", response_metadata={"model_name": "models/gemini-3.5-flash-lite-001"})
    agente, _ = _agente(proyecto, [servido, AIMessage("sin metadatos")])
    assert agente.conversar("s1", "hola").modelo == "gemini-3.5-flash-lite-001"
    assert agente.conversar("s1", "otra").modelo == "guion"  # sin metadatos: el configurado en el adaptador
