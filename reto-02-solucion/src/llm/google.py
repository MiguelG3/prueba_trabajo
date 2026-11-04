"""Gemini nativo (Google AI Studio, free tier). Maneja las thought signatures que exige el tool calling multi-turno."""

from langchain_google_genai import ChatGoogleGenerativeAI

from src.config import Settings


def crear_chat(settings: Settings) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=settings.modelo,
        google_api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        **({"temperature": settings.llm_temperature} if settings.llm_temperature is not None else {}),
    )
