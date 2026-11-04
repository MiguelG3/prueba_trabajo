"""Interfaz propia del proveedor LLM: `enviar(mensajes, herramientas) -> RespuestaLLM`.

El ciclo del agente (src/graph.py) solo conoce esta interfaz; cambiar de proveedor no lo toca.
"""

import copy
from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from src.config import Settings
from src.llm.presets import PRESETS


@dataclass(frozen=True)
class HerramientaLLM:
    nombre: str
    descripcion: str
    parametros: dict  # JSON schema (pydantic) de los argumentos


@dataclass
class RespuestaLLM:
    mensaje: AIMessage
    tool_calls: list[dict] = field(default_factory=list)
    tokens: int = 0

    @property
    def texto(self) -> str:
        return texto_de(self.mensaje)


def texto_de(mensaje: BaseMessage) -> str:
    """Texto visible de un mensaje (ignora bloques de razonamiento o tool_use)."""
    if isinstance(mensaje.content, str):
        return mensaje.content
    return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else b if isinstance(b, str) else ""
                   for b in mensaje.content)


class LLMAdapter(Protocol):
    proveedor: str
    modelo: str
    tier: str

    def enviar(self, mensajes: list[BaseMessage], herramientas: list[HerramientaLLM]) -> RespuestaLLM: ...


# ---------- JSON schema → formato de herramienta ----------

# `default` se conserva: langchain-google-genai marca como obligatorio todo campo anidado sin default.
_CLAVES_NO_SOPORTADAS_SIMPLE = {"pattern", "format", "additionalProperties", "maxLength", "minLength"}


def _resolver_refs(nodo: object, defs: dict) -> object:
    if isinstance(nodo, dict):
        if "$ref" in nodo:
            return _resolver_refs(copy.deepcopy(defs[nodo["$ref"].split("/")[-1]]), defs)
        return {k: _resolver_refs(v, defs) for k, v in nodo.items() if k != "$defs"}
    if isinstance(nodo, list):
        return [_resolver_refs(v, defs) for v in nodo]
    return nodo


def _simplificar(nodo: object) -> object:
    """Para proveedores con soporte parcial de JSON schema (Gemini, Ollama): colapsa `anyOf [X, null]` y quita claves no soportadas."""
    if isinstance(nodo, list):
        return [_simplificar(v) for v in nodo]
    if not isinstance(nodo, dict):
        return nodo
    if "anyOf" in nodo:
        no_nulos = [o for o in nodo["anyOf"] if o.get("type") != "null"]
        if len(no_nulos) == 1:
            fusion = {**no_nulos[0], **{k: v for k, v in nodo.items() if k != "anyOf"}}
            return _simplificar(fusion)
    return {k: _simplificar(v) for k, v in nodo.items() if k not in _CLAVES_NO_SOPORTADAS_SIMPLE}


def _sin_titulos(nodo: object) -> object:
    if isinstance(nodo, dict):
        return {k: _sin_titulos(v) for k, v in nodo.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(nodo, list):
        return [_sin_titulos(v) for v in nodo]
    return nodo


def a_formato_openai(herramienta: HerramientaLLM, schema_simple: bool) -> dict:
    parametros = _sin_titulos(_resolver_refs(herramienta.parametros, herramienta.parametros.get("$defs", {})))
    if schema_simple:
        parametros = _simplificar(parametros)
    parametros.setdefault("properties", {})
    parametros["type"] = "object"
    return {"type": "function", "function": {"name": herramienta.nombre, "description": herramienta.descripcion, "parameters": parametros}}


# ---------- implementación genérica sobre LangChain ----------


class LangChainAdapter:
    """Adapta cualquier `BaseChatModel` de LangChain (OpenAI-compatible o Anthropic) a la interfaz propia."""

    def __init__(self, chat: BaseChatModel, proveedor: str, modelo: str, tier: str, schema_simple: bool):
        self._chat = chat
        self.proveedor, self.modelo, self.tier = proveedor, modelo, tier
        self._schema_simple = schema_simple

    def enviar(self, mensajes: list[BaseMessage], herramientas: list[HerramientaLLM]) -> RespuestaLLM:
        chat = self._chat.bind_tools([a_formato_openai(h, self._schema_simple) for h in herramientas]) if herramientas else self._chat
        mensaje = chat.invoke(mensajes)
        uso = getattr(mensaje, "usage_metadata", None) or {}
        return RespuestaLLM(mensaje=mensaje, tool_calls=list(mensaje.tool_calls), tokens=int(uso.get("total_tokens", 0)))


def crear_adapter(settings: Settings) -> LLMAdapter:
    preset = PRESETS[settings.llm_provider]
    if preset.requiere_key and not settings.llm_api_key:
        raise ValueError(
            f"Falta LLM_API_KEY para '{settings.llm_provider}'. {preset.como_obtener_key}. "
            "Alternativa sin llave: LLM_PROVIDER=ollama."
        )
    if preset.familia == "anthropic":
        from src.llm.anthropic import crear_chat
    elif preset.familia == "google":
        from src.llm.google import crear_chat
    else:
        from src.llm.openai_compat import crear_chat
    return LangChainAdapter(crear_chat(settings), settings.llm_provider, settings.modelo, settings.tier, preset.schema_simple)
