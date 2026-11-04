from modulo.tools import contratos as modulo_tools
from scripts.build_modulo import generar
from src.tools import contratos


def test_modulo_no_diverge_de_las_fuentes():
    for ruta, esperado in generar().items():
        assert ruta.read_text(encoding="utf-8") == esperado, f"{ruta.name} desactualizado: uv run python scripts/build_modulo.py"


def test_modulo_reexporta_las_mismas_herramientas():
    assert modulo_tools.HERRAMIENTAS is contratos.HERRAMIENTAS
    assert modulo_tools.registrar is contratos.registrar
