"""Contrato de herramientas: `description` + `args` (pydantic) + `execute(args, ctx) -> str` que nunca lanza."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from src.dominio.maestro import Rutas, agregar_jsonl, ahora_iso


@dataclass(frozen=True)
class Contexto:
    """ctx del PRD: `directory` = raíz del proyecto; `hoy` permite demos deterministas."""

    directory: Path
    session_id: str = "demo"
    hoy: date = field(default_factory=date.today)

    @property
    def rutas(self) -> Rutas:
        return Rutas(Path(self.directory))


class ToolError(Exception):
    """Error esperado y legible; `detalle` viaja al modelo junto con el mensaje."""

    def __init__(self, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.detalle = detalle


_ERRORES_LEGIBLES = (ToolError, ValueError, FileNotFoundError, PermissionError, KeyError)


def _formatear_validacion(exc: ValidationError) -> str:
    partes = [f"{'.'.join(str(p) for p in e['loc']) or 'args'}: {e['msg']}" for e in exc.errors()]
    return "argumentos inválidos — " + "; ".join(partes)


@dataclass(frozen=True)
class Tool:
    nombre: str
    description: str
    args: type[BaseModel]
    run: Callable[[Any, Contexto], dict]
    resumir: Callable[[dict], str] = lambda data: "ok"

    def execute(self, args: dict | None, ctx: Contexto) -> str:
        """Valida, ejecuta y registra en out/log.jsonl. Devuelve JSON `{ok, data}` o `{ok: false, error}`."""
        args = args or {}
        mensaje_id = args.get("mensaje_id") if isinstance(args, dict) else None
        try:
            data = self.run(self.args.model_validate(args), ctx)
            respuesta = {"ok": True, "data": data}
            resumen = self.resumir(data)
        except ValidationError as exc:
            respuesta = {"ok": False, "error": _formatear_validacion(exc)}
            resumen = respuesta["error"]
        except _ERRORES_LEGIBLES as exc:
            respuesta = {"ok": False, "error": str(exc).strip("'\"")}
            if isinstance(exc, ToolError) and exc.detalle:
                respuesta["detalle"] = exc.detalle
            resumen = respuesta["error"]
        except Exception as exc:  # noqa: BLE001 — la herramienta nunca lanza hacia el agente
            respuesta = {"ok": False, "error": f"error interno en {self.nombre} ({type(exc).__name__}); el caso se puede reintentar"}
            resumen = f"{type(exc).__name__}: {exc}"
        self._log(ctx, mensaje_id, respuesta["ok"], resumen)
        return json.dumps(respuesta, ensure_ascii=False, default=str)

    def json_schema(self) -> dict:
        return self.args.model_json_schema()

    def _log(self, ctx: Contexto, mensaje_id: str | None, ok: bool, resumen: str) -> None:
        try:
            agregar_jsonl(ctx.rutas.log, {
                "ts": ahora_iso(), "herramienta": self.nombre, "mensaje_id": mensaje_id,
                "ok": ok, "resumen": resumen[:300], "session_id": ctx.session_id,
            })
        except OSError:
            pass  # el log nunca debe tumbar la herramienta
