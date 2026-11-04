"""Fechas escritas en contratos en español y aritmética de plazos."""

import calendar
import re
from datetime import date, timedelta

from src.dominio.texto import sin_tildes

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_MES = "|".join(MESES)

# "primero (1) de agosto de 2026" · "15 de agosto de 2026" · "(30) días del mes de julio de 2026"
FECHA_COMPLETA = re.compile(
    rf"(?:\((\d{{1,2}})\)|\b(\d{{1,2}}))\s+(?:dias\s+del\s+mes\s+)?de\s+({_MES})\s+de\s+(\d{{4}})",
    re.IGNORECASE,
)
# "en el mes de agosto de 2026" (sin día)
SOLO_MES = re.compile(rf"\bmes\s+de\s+({_MES})\s+de\s+(\d{{4}})", re.IGNORECASE)


class FechaInvalida(ValueError):
    pass


def construir_fecha(anio: int, mes: int, dia: int, origen: str) -> date:
    try:
        return date(anio, mes, dia)
    except ValueError as exc:
        raise FechaInvalida(f"fecha inválida en el documento: '{origen}'") from exc


def buscar_fechas(texto: str) -> list[tuple[date, str]]:
    """Todas las fechas con día explícito, en orden de aparición, con el fragmento de origen."""
    normal = sin_tildes(texto)
    resultado = []
    for m in FECHA_COMPLETA.finditer(normal):
        dia = int(m.group(1) or m.group(2))
        resultado.append((construir_fecha(int(m.group(4)), MESES[m.group(3).lower()], dia, m.group(0)), m.group(0)))
    return resultado


def primera_fecha(texto: str) -> tuple[date, str] | None:
    fechas = buscar_fechas(texto)
    return fechas[0] if fechas else None


def buscar_solo_mes(texto: str) -> tuple[int, int, str] | None:
    """(año, mes, fragmento) para expresiones tipo 'en el mes de agosto de 2026'."""
    m = SOLO_MES.search(sin_tildes(texto))
    return (int(m.group(2)), MESES[m.group(1).lower()], m.group(0)) if m else None


def sumar_meses(inicio: date, meses: int) -> date:
    mes_total = inicio.month - 1 + meses
    anio, mes = inicio.year + mes_total // 12, mes_total % 12 + 1
    return date(anio, mes, min(inicio.day, calendar.monthrange(anio, mes)[1]))


def fin_por_plazo(inicio: date, meses: int) -> date:
    """Convención de los contratos del buzón: 15-ago-2026 + 12 meses -> 14-ago-2027."""
    return sumar_meses(inicio, meses) - timedelta(days=1)


def ultimo_dia_mes(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])
