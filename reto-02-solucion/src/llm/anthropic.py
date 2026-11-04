"""Claude (Anthropic) nativo vía langchain-anthropic.

- Los modelos Claude actuales rechazan `temperature` (400): no se envía.
- Fallback server-side ante rechazos de seguridad (`fallbacks: "default"`) activado por defecto en los
  modelos que lo soportan; se desactiva con LLM_ANTHROPIC_FALLBACK=false.
- La profundidad de razonamiento se controla con `output_config.effort` (LLM_EFFORT).
"""

from langchain_anthropic import ChatAnthropic

from src.config import Settings

MODELOS_CON_FALLBACK = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
BETA_FALLBACK = "server-side-fallback-2026-07-01"


def crear_chat(settings: Settings) -> ChatAnthropic:
    extra: dict = {}
    betas: list[str] = []
    if settings.llm_effort:
        extra["output_config"] = {"effort": settings.llm_effort}
    if settings.llm_anthropic_fallback and settings.modelo in MODELOS_CON_FALLBACK:
        betas.append(BETA_FALLBACK)
        extra["fallbacks"] = "default"
    opcionales = {"base_url": settings.llm_base_url} if settings.llm_base_url else {}
    return ChatAnthropic(
        model=settings.modelo,
        api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        max_tokens=16000,
        betas=betas or None,
        model_kwargs=extra,
        **opcionales,
    )
