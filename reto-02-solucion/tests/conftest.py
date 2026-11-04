import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from src.llm.adapter import RespuestaLLM
from src.tools.base import Contexto

RAIZ = Path(__file__).resolve().parent.parent
HOY = date(2026, 9, 3)


@pytest.fixture
def proyecto(tmp_path: Path) -> Path:
    """Copia aislada del proyecto: cada test escribe su propio out/."""
    for carpeta in ("fixtures", "agent", "src/knowledge"):
        shutil.copytree(RAIZ / carpeta, tmp_path / carpeta)
    return tmp_path


@pytest.fixture
def ctx(proyecto: Path) -> Contexto:
    return Contexto(directory=proyecto, session_id="test", hoy=HOY)


def llamar(tool, args: dict, ctx: Contexto) -> dict:
    return json.loads(tool.execute(args, ctx))


def crear_mensaje(proyecto: Path, mensaje_id: str, adjunto: str | None, texto: str = "", de: str = "lgomez@periferia-ficticia.com") -> None:
    carpeta = proyecto / "fixtures" / "reto-02" / "buzon" / mensaje_id
    carpeta.mkdir(parents=True)
    correo = {"id": mensaje_id, "de": de, "para": "contratos@periferia-ficticia.com", "asunto": "prueba",
              "fecha": "2026-08-30T10:00:00-05:00", "cuerpo": "", "adjuntos": [adjunto] if adjunto else []}
    (carpeta / "correo.json").write_text(json.dumps(correo), encoding="utf-8")
    if adjunto:
        (carpeta / adjunto).write_text(texto, encoding="utf-8")


class ScriptedAdapter:
    """Adaptador LLM guionado para probar el grafo sin red. Registra qué herramientas recibió cada llamada."""

    proveedor, modelo, tier = "scripted", "guion", "gratis"

    def __init__(self, respuestas: list):
        self.respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    def enviar(self, mensajes, herramientas):
        self.llamadas.append({"mensajes": list(mensajes), "herramientas": [h.nombre for h in herramientas]})
        siguiente = self.respuestas.pop(0) if self.respuestas else AIMessage("listo")
        if isinstance(siguiente, Exception):
            raise siguiente
        if callable(siguiente):
            siguiente = siguiente(mensajes, herramientas)
        return RespuestaLLM(mensaje=siguiente, tool_calls=list(siguiente.tool_calls), tokens=100)


def tool_call(nombre: str, args: dict, id_: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": nombre, "args": args, "id": id_, "type": "tool_call"}])
