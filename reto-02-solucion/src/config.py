"""Configuración tipada desde variables de entorno (.env). La llave nunca sale de aquí."""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.llm.presets import PRESETS, Proveedor

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=RAIZ_PROYECTO / ".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Proveedor = Field("gemini", description="Proveedor del modelo; ver src/llm/presets.py")
    llm_model: str | None = Field(None, description="Modelo; si se omite se usa el del preset")
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = Field(None, description="Sobrescribe la URL del preset (Azure, vLLM, proxy...)")
    llm_temperature: float | None = Field(None, ge=0, le=2, description="Solo proveedores OpenAI-compatibles; Claude actual la rechaza")
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(None, description="Claude: output_config.effort")
    llm_anthropic_fallback: bool = Field(True, description="Claude: fallback server-side ante rechazos")
    llm_timeout_s: float = Field(60, gt=0)
    llm_max_retries: int = Field(3, ge=0, le=10, description="Reintentos con backoff ante 429/5xx (free tiers)")

    max_iteraciones: int = Field(25, ge=1, le=100, description="CA1: tope herramienta→modelo por turno")
    max_tokens_sesion: int = Field(300_000, ge=1_000, description="Tope de tokens por sesión")
    max_tokens_global: int = Field(3_000_000, ge=10_000, description="Tope de tokens del proceso (protege la llave)")
    app_access_key: SecretStr | None = Field(None, description="Si se define, el front debe enviarla en X-Access-Key")
    fecha_referencia: date | None = Field(None, description="Fija 'hoy' para fecha_registro (demos deterministas)")
    port: int = 8000

    @model_validator(mode="after")
    def _validar_proveedor(self) -> "Settings":
        preset = PRESETS[self.llm_provider]
        if not (self.llm_model or preset.modelo_por_defecto):
            raise ValueError(f"LLM_MODEL es obligatorio para el proveedor '{self.llm_provider}'")
        if self.llm_provider == "openai_compatible" and not self.llm_base_url:
            raise ValueError("LLM_BASE_URL es obligatorio para LLM_PROVIDER=openai_compatible")
        return self

    @property
    def modelo(self) -> str:
        return self.llm_model or PRESETS[self.llm_provider].modelo_por_defecto

    @property
    def base_url(self) -> str | None:
        return self.llm_base_url or PRESETS[self.llm_provider].base_url

    @property
    def tier(self) -> str:
        return PRESETS[self.llm_provider].tier


@lru_cache
def get_settings() -> Settings:
    return Settings()
