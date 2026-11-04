"""Normalización de texto: tildes, slugs y comparación de objetos contractuales."""

import re
import unicodedata
from difflib import SequenceMatcher

_SUFIJOS_SOCIETARIOS = r"\b(s\.?\s?a\.?\s?s\.?|s\.?\s?a\.?\s?c\.?|s\.?\s?a\.?|s\.?\s?de\s?r\.?\s?l\.?|ltda\.?|e\.?\s?u\.?)\s*$"
_STOPWORDS = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "para", "por", "a", "al", "con", "un", "una"}


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def slug_cliente(nombre: str) -> str:
    """'Corporación Andina de Servicios S.A.' -> 'corporacion-andina-de-servicios'."""
    base = sin_tildes(nombre.lower()).strip()
    base = re.sub(_SUFIJOS_SOCIETARIOS, "", base).strip(" .,")
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")


def normalizar_para_comparar(texto: str) -> str:
    palabras = re.findall(r"[a-z0-9]+", sin_tildes(texto.lower()))
    return " ".join(p for p in palabras if p not in _STOPWORDS)


def similitud(a: str, b: str) -> float:
    """Similitud [0, 1] entre dos descripciones, insensible a tildes, mayúsculas y stopwords."""
    return SequenceMatcher(None, normalizar_para_comparar(a), normalizar_para_comparar(b)).ratio()


def colapsar_espacios(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip()
