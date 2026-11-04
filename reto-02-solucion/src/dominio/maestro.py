"""Repositorio de archivos: buzón (solo lectura), maestro CSV en out/sharepoint, historial y procesados.

El fixture `maestro-contratos.csv` nunca se modifica (RN6): la primera escritura lo copia a out/.
Las escrituras son atómicas (archivo temporal + rename) y serializadas con un lock de proceso.
"""

import csv
import io
import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from src.tools.schemas import FilaMaestro

COLUMNAS = list(FilaMaestro.model_fields)
LOCK = threading.RLock()


@dataclass(frozen=True)
class Rutas:
    raiz: Path

    @property
    def fixtures(self) -> Path:
        return self.raiz / "fixtures" / "reto-02"

    @property
    def buzon(self) -> Path:
        return self.fixtures / "buzon"

    @property
    def maestro_fixture(self) -> Path:
        return self.fixtures / "maestro-contratos.csv"

    @property
    def comerciales(self) -> Path:
        return self.fixtures / "comerciales.json"

    @property
    def out(self) -> Path:
        return self.raiz / "out"

    @property
    def sharepoint(self) -> Path:
        return self.out / "sharepoint"

    @property
    def maestro(self) -> Path:
        return self.sharepoint / "maestro-contratos.csv"

    @property
    def historial(self) -> Path:
        return self.sharepoint / "historial.jsonl"

    @property
    def procesados(self) -> Path:
        return self.out / "procesados.json"

    @property
    def log(self) -> Path:
        return self.out / "log.jsonl"

    @property
    def alertas(self) -> Path:
        return self.out / "alertas.md"

    def dentro_de_proyecto(self, relativa: str) -> Path:
        """Resuelve una ruta relativa y garantiza que quede dentro de fixtures/ u out/ (anti path traversal)."""
        ruta = (self.raiz / relativa).resolve()
        permitidas = (self.fixtures.resolve(), self.out.resolve())
        if not any(ruta.is_relative_to(base) for base in permitidas):
            raise PermissionError(f"ruta fuera de fixtures/ u out/: '{relativa}'")
        return ruta


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def escribir_atomico(ruta: Path, contenido: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}.")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(contenido)
    os.replace(tmp, ruta)


def agregar_jsonl(ruta: Path, registro: dict) -> None:
    with LOCK:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")


def leer_jsonl(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- buzón ----------


def leer_correo(rutas: Rutas, mensaje_id: str) -> dict:
    carpeta = rutas.buzon / mensaje_id
    if not carpeta.is_dir():
        raise FileNotFoundError(f"no existe el mensaje '{mensaje_id}' en el buzón")
    try:
        return json.loads((carpeta / "correo.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"el mensaje '{mensaje_id}' no tiene correo.json") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"correo.json de '{mensaje_id}' está malformado: {exc.msg}") from exc


def listar_mensajes(rutas: Rutas) -> list[str]:
    return sorted(p.name for p in rutas.buzon.iterdir() if p.is_dir()) if rutas.buzon.is_dir() else []


# ---------- maestro ----------


def _a_csv(fila: FilaMaestro) -> dict[str, str]:
    datos = fila.model_dump()
    datos["valor"] = f"{fila.valor:.0f}" if fila.valor == int(fila.valor) else f"{fila.valor}"
    datos["requiere_poliza"] = "true" if fila.requiere_poliza else "false"
    return {k: str(v) for k, v in datos.items()}


def _desde_csv(fila: dict[str, str]) -> FilaMaestro:
    datos = dict(fila)
    datos["requiere_poliza"] = fila["requiere_poliza"].strip().lower() == "true"
    return FilaMaestro(**datos)


def asegurar_maestro(rutas: Rutas) -> Path:
    """RN6: la primera ejecución copia el fixture a out/sharepoint/ (el fixture es de solo lectura)."""
    with LOCK:
        if not rutas.maestro.exists():
            rutas.sharepoint.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rutas.maestro_fixture, rutas.maestro)
    return rutas.maestro


def leer_maestro(rutas: Rutas) -> list[FilaMaestro]:
    fuente = rutas.maestro if rutas.maestro.exists() else rutas.maestro_fixture
    with fuente.open(encoding="utf-8", newline="") as f:
        return [_desde_csv(fila) for fila in csv.DictReader(f)]


def guardar_maestro(rutas: Rutas, filas: list[FilaMaestro]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNAS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(_a_csv(f) for f in filas)
    escribir_atomico(rutas.maestro, buffer.getvalue())


def buscar_por_id(filas: list[FilaMaestro], id_contrato: str | None) -> FilaMaestro | None:
    return next((f for f in filas if id_contrato and f.id_contrato == id_contrato), None)


def siguiente_id_auto(filas: list[FilaMaestro], anio: int) -> str:
    prefijo = f"AUTO-{anio}-"
    usados = [int(f.id_contrato.removeprefix(prefijo)) for f in filas if f.id_contrato.startswith(prefijo)]
    return f"{prefijo}{max(usados, default=0) + 1:03d}"


# ---------- procesados ----------


def leer_procesados(rutas: Rutas) -> dict[str, dict]:
    return json.loads(rutas.procesados.read_text(encoding="utf-8")) if rutas.procesados.exists() else {}


def marcar_procesado(rutas: Rutas, mensaje_id: str, resultado: dict) -> None:
    with LOCK:
        procesados = leer_procesados(rutas)
        procesados[mensaje_id] = resultado
        escribir_atomico(rutas.procesados, json.dumps(procesados, ensure_ascii=False, indent=2, default=str))


def archivar(rutas: Rutas, origen: Path, anio: int, slug: str, nombre: str) -> str:
    """Copia el documento a out/sharepoint/Contratos/<año>/<slug>/<nombre>; devuelve la ruta relativa a sharepoint/."""
    relativa = Path("Contratos") / str(anio) / slug / f"{nombre}{origen.suffix}"
    destino = rutas.sharepoint / relativa
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origen, destino)
    return relativa.as_posix()


def fecha_iso(valor: date | str) -> str:
    return valor.isoformat() if isinstance(valor, date) else str(valor)
