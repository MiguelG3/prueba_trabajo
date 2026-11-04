"""Cualquier API compatible con OpenAI: Gemini, Groq, OpenRouter, Ollama (gratis) · OpenAI, Azure, Mistral... (pago)."""

from langchain_openai import ChatOpenAI

from src.config import Settings


def crear_chat(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.modelo,
        base_url=settings.base_url,
        api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else "sin-llave",
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,  # backoff exponencial del SDK ante 429/5xx
        **({"temperature": settings.llm_temperature} if settings.llm_temperature is not None else {}),
    )
