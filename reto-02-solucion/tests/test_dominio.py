import json
from datetime import date
from pathlib import Path

import pytest

from src.dominio.extraccion import ExtraccionError, extraer_contrato
from src.dominio.fechas import FechaInvalida, buscar_fechas, fin_por_plazo, sumar_meses
from src.dominio.numeros import palabras_a_numero, parsear_monto
from src.dominio.texto import similitud, slug_cliente

BUZON = Path(__file__).resolve().parent.parent / "fixtures" / "reto-02" / "buzon"


@pytest.mark.parametrize("texto, esperado", [
    ("DOSCIENTOS SESENTA Y CINCO MILLONES DE PESOS M/CTE", 265_000_000),
    ("CIENTO VEINTE MIL DÓLARES DE LOS ESTADOS UNIDOS", 120_000),
    ("QUINIENTOS VEINTE MIL SOLES", 520_000),
    ("UN MILLÓN DOSCIENTOS MIL PESOS", 1_200_000),
    ("MIL DOSCIENTOS MILLONES", 1_200_000_000),
    ("sin números", None),
])
def test_palabras_a_numero(texto, esperado):
    assert palabras_a_numero(texto) == esperado


@pytest.mark.parametrize("texto, esperado", [
    ("265.000.000", 265_000_000), ("120,000.00", 120_000), ("1.234,50", 1234.5), ("520,000.00", 520_000),
    ("1.500", 1500), ("12,5", 12.5), ("$ 3.000.000", 3_000_000),
])
def test_parsear_monto(texto, esperado):
    assert parsear_monto(texto) == esperado


def test_parsear_monto_no_numerico():
    with pytest.raises(ValueError, match="no numérico"):
        parsear_monto("abc")


def test_plazos():
    assert fin_por_plazo(date(2026, 8, 15), 12) == date(2027, 8, 14)
    assert fin_por_plazo(date(2026, 5, 15), 6) == date(2026, 11, 14)
    assert sumar_meses(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_fecha_invalida_es_error_legible():
    with pytest.raises(FechaInvalida, match="fecha inválida"):
        buscar_fechas("desde el treinta y uno (31) de febrero de 2026")


def test_slug_y_similitud():
    assert slug_cliente("Corporación Andina de Servicios S.A.") == "corporacion-andina-de-servicios"
    assert slug_cliente("Agroexport Sula S. de R.L.") == "agroexport-sula"
    assert slug_cliente("Minera Los Andes S.A.C.") == "minera-los-andes"
    assert similitud("Mesa de servicio TI nivel 1 y 2", "mesa de servicio ti, nivel 1 y 2") > 0.95


def _extraer(mensaje_id: str):
    correo = json.loads((BUZON / mensaje_id / "correo.json").read_text())
    adjunto = correo["adjuntos"][0]
    return extraer_contrato(mensaje_id, adjunto, (BUZON / mensaje_id / adjunto).read_text(), date.fromisoformat(correo["fecha"][:10]))


GOLDEN = {
    "msg-001": {"tipo": "contrato", "id_contrato": "CT-2026-015", "nit_cliente": "890900111", "pais": "CO", "valor": 265_000_000,
                "moneda": "COP", "fecha_inicio": date(2026, 8, 1), "fecha_fin": date(2027, 7, 31), "requiere_poliza": True,
                "tipo_poliza": "cumplimiento", "estado_poliza": "pendiente"},
    "msg-002": {"tipo": "contrato", "id_contrato": "CT-2026-016", "nit_cliente": "1790012345001", "pais": "EC", "valor": 120_000,
                "moneda": "USD", "fecha_inicio": date(2026, 8, 15), "fecha_fin": date(2027, 8, 14), "requiere_poliza": False,
                "estado_poliza": "no_aplica"},
    "msg-003": {"tipo": "otrosi", "id_contrato": "CT-2026-011", "nit_cliente": "20512345678", "pais": "PE", "valor": 520_000,
                "moneda": "PEN", "fecha_fin": date(2027, 11, 1), "estado_poliza": "pendiente", "fecha_inicio": None},
    "msg-004": {"tipo": "contrato", "id_contrato": "CT-2026-012", "valor": 210_000_000, "fecha_inicio": date(2026, 5, 15),
                "fecha_fin": date(2026, 11, 14)},
    "msg-005": {"tipo": "otro", "id_contrato": None},
    "msg-006": {"tipo": "contrato_marco", "id_contrato": "CM-2026-03", "nit_cliente": "800222333", "valor": 0, "moneda": "COP",
                "requiere_poliza": True, "tipo_poliza": "cumplimiento"},
}


@pytest.mark.parametrize("mensaje_id", sorted(GOLDEN))
def test_extraccion_golden(mensaje_id):
    extraido = _extraer(mensaje_id)
    esperado = dict(GOLDEN[mensaje_id])
    assert extraido.tipo_documento == esperado.pop("tipo")
    for campo, valor in esperado.items():
        assert getattr(extraido, campo).valor == valor, campo


def test_confianza_msg_006_solo_valor_y_fecha_fin_bajo_umbral():
    extraido = _extraer("msg-006")
    bajos = {k for k, c in extraido.campos().items() if c.confianza < 0.8}
    assert bajos == {"valor", "fecha_fin"}
    assert extraido.valor_indeterminado
    assert any("condicional" in a for a in extraido.advertencias)


def test_evidencia_literal_del_documento():
    extraido = _extraer("msg-001")
    assert "COP $265.000.000" in extraido.valor.evidencia
    assert extraido.valor.confianza == 0.95  # número verificado contra el valor en letras


def test_texto_vacio_y_moneda_desconocida():
    with pytest.raises(ExtraccionError, match="vacío"):
        extraer_contrato("x", "contrato.txt", "   ", None)
    texto = "CONTRATO No. CT-2026-099\n\nSEGUNDA. VALOR. El valor es de MIL EUROS (EUR 1.000).\n"
    with pytest.raises(ExtraccionError, match="moneda desconocida 'EUR'"):
        extraer_contrato("x", "contrato.txt", texto, None)


def test_valor_en_letras_inconsistente_baja_confianza():
    texto = ("CONTRATO No. CT-2026-098\n\nEntre ACME S.A.S., identificada con NIT 800.111.222-3, y PERIFERIA IT GROUP S.A.S., "
             "identificada con NIT 900.123.456-7.\n\nSEGUNDA. VALOR. El valor es de DIEZ MILLONES DE PESOS (COP $100.000.000).\n")
    extraido = extraer_contrato("x", "contrato.txt", texto, None)
    assert extraido.valor.confianza == 0.5 and "no coincide" in extraido.valor.nota
