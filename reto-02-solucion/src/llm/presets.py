"""Proveedores soportados. Cambiar de proveedor o modelo es solo configuración (LLM_PROVIDER / LLM_MODEL)."""

from dataclasses import dataclass
from typing import Literal

Proveedor = Literal["gemini", "groq", "openrouter", "ollama", "openai", "anthropic", "openai_compatible"]
Tier = Literal["gratis", "pago"]


@dataclass(frozen=True)
class Preset:
    tier: Tier
    familia: Literal["openai", "anthropic", "google"]  # qué implementación del adaptador usa
    modelo_por_defecto: str
    base_url: str | None
    requiere_key: bool
    schema_simple: bool  # proveedores que no aceptan JSON schema completo (anyOf, pattern, additionalProperties)
    como_obtener_key: str


PRESETS: dict[str, Preset] = {
    "gemini": Preset(
        "gratis", "google", "gemini-3.5-flash-lite", None, True, True,
        "Gratis y sin tarjeta en https://aistudio.google.com/apikey",
    ),
    "groq": Preset(
        "gratis", "openai", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1", True, False,
        "Gratis en https://console.groq.com/keys",
    ),
    "openrouter": Preset(
        "gratis", "openai", "meta-llama/llama-3.3-70b-instruct:free", "https://openrouter.ai/api/v1", True, False,
        "Gratis en https://openrouter.ai/keys (modelos con sufijo ':free')",
    ),
    "ollama": Preset(
        "gratis", "openai", "qwen2.5:7b", "http://localhost:11434/v1", False, True,
        "Local, sin llave: https://ollama.com (ollama pull qwen2.5:7b)",
    ),
    "openai": Preset("pago", "openai", "gpt-4.1-mini", None, True, False, "https://platform.openai.com/api-keys"),
    "anthropic": Preset("pago", "anthropic", "claude-opus-5-5", None, True, False, "https://console.anthropic.com/settings/keys"),
    "openai_compatible": Preset(
        "pago", "openai", "", None, True, False,
        "Cualquier endpoint compatible con OpenAI (Azure, Mistral, Together, vLLM...): define LLM_BASE_URL y LLM_MODEL",
    ),
}
