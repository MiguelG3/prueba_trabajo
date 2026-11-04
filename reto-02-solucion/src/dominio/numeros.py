"""Números en español: montos con separadores y valores escritos en letras."""

import re

from src.dominio.texto import sin_tildes

_UNIDADES = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiun": 21, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
_MULTIPLICADORES = {"millon": 10**6, "millones": 10**6, "billon": 10**12, "billones": 10**12}


def palabras_a_numero(texto: str) -> int | None:
    """'DOSCIENTOS SESENTA Y CINCO MILLONES DE PESOS' -> 265000000. None si no hay números en letras."""
    total, actual, encontrado = 0, 0, False
    for palabra in re.findall(r"[a-z]+", sin_tildes(texto.lower())):
        if palabra in _UNIDADES:
            actual += _UNIDADES[palabra]
            encontrado = True
        elif palabra == "mil":
            actual = max(actual, 1) * 1000
            encontrado = True
        elif palabra in _MULTIPLICADORES:
            total += max(actual, 1) * _MULTIPLICADORES[palabra]
            actual = 0
            encontrado = True
    return total + actual if encontrado else None


def parsear_monto(texto: str) -> float:
    """'265.000.000' -> 265000000 · '120,000.00' -> 120000 · '1.234,5' -> 1234.5.

    Regla: si hay ambos separadores, el último es el decimal. Si solo hay uno, es de miles
    cuando se repite o va seguido de exactamente 3 dígitos; si no, es decimal.
    """
    limpio = re.sub(r"[^\d.,]", "", texto)
    if not re.search(r"\d", limpio):
        raise ValueError(f"monto no numérico: '{texto}'")
    if "." in limpio and "," in limpio:
        decimal = "." if limpio.rfind(".") > limpio.rfind(",") else ","
        miles = "," if decimal == "." else "."
        return float(limpio.replace(miles, "").replace(decimal, "."))
    for sep in (".", ","):
        if sep in limpio:
            partes = limpio.split(sep)
            if len(partes) > 2 or len(partes[-1]) == 3:
                return float(limpio.replace(sep, ""))
            return float(limpio.replace(sep, "."))
    return float(limpio)
