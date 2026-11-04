import csv
import hashlib
import json

from src.tools.contratos import alertas, extraer, leer_buzon, leer_pdf, registrar, validar
from tests.conftest import HOY, crear_mensaje, llamar

ESPERADO = {
    "msg-001": ("nuevo", "insertado", "CT-2026-015"),
    "msg-002": ("nuevo", "insertado", "CT-2026-016"),
    "msg-003": ("actualizacion", "actualizado", "CT-2026-011"),
    "msg-004": ("duplicado", "sin_cambios", "CT-2026-012"),
    "msg-005": ("rechazado", "rechazado", None),
}


def _procesar_buzon(ctx):
    resultados = {}
    for m in llamar(leer_buzon, {}, ctx)["data"]["mensajes"]:
        resultados[m["id"]] = (llamar(validar, {"mensaje_id": m["id"]}, ctx)["data"], llamar(registrar, {"mensaje_id": m["id"]}, ctx))
    return resultados


def _maestro(ctx) -> list[dict]:
    with ctx.rutas.maestro.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_leer_buzon_marca_si_trae_contrato(ctx):
    mensajes = {m["id"]: m for m in llamar(leer_buzon, {}, ctx)["data"]["mensajes"]}
    assert len(mensajes) == 6
    assert mensajes["msg-005"]["tiene_contrato"] is False
    assert all(mensajes[m]["tiene_contrato"] for m in ("msg-001", "msg-002", "msg-003", "msg-004", "msg-006"))


def test_flujo_completo_del_buzon(ctx):
    resultados = _procesar_buzon(ctx)
    for mensaje_id, (clasificacion, accion, id_contrato) in ESPERADO.items():
        validacion, registro = resultados[mensaje_id]
        assert validacion["clasificacion"] == clasificacion
        assert registro["ok"] and registro["data"]["accion"] == accion and registro["data"]["id_contrato"] == id_contrato

    validacion, registro = resultados["msg-006"]
    assert validacion["clasificacion"] == "nuevo"
    assert validacion["requiere_revision"] == ["valor", "fecha_fin"]
    assert any("jperez" in a for a in validacion["advertencias"])  # remitente desconocido: se reporta, no bloquea
    assert registro == {"ok": False, "error": "requiere revisión: valor, fecha_fin", "detalle": registro["detalle"]}

    filas = {f["id_contrato"]: f for f in _maestro(ctx)}
    assert "CM-2026-03" not in filas
    assert filas["CT-2026-015"]["estado_poliza"] == "pendiente"
    assert filas["CT-2026-016"]["estado_poliza"] == "no_aplica"
    assert (filas["CT-2026-011"]["fecha_fin"], filas["CT-2026-011"]["valor"]) == ("2027-11-01", "520000")
    assert (ctx.rutas.sharepoint / filas["CT-2026-015"]["ruta_sharepoint"]).is_file()
    assert len(filas) == len(_maestro(ctx)), "sin filas duplicadas"

    historial = [json.loads(l) for l in ctx.rutas.historial.read_text().splitlines()]
    otrosi = next(h for h in historial if h["id_contrato"] == "CT-2026-011")
    assert otrosi["cambios"]["fecha_fin"] == {"antes": "2027-05-01", "despues": "2027-11-01"}
    assert otrosi["mensaje_id"] == "msg-003"


def test_confirmacion_registra_msg_006_con_valores_del_usuario(ctx):
    _procesar_buzon(ctx)
    r = llamar(registrar, {"mensaje_id": "msg-006", "contrato": {"valor": 0, "fecha_fin": "2027-08-31"}, "confirmado": True}, ctx)
    assert r["ok"] and r["data"]["accion"] == "insertado"
    assert r["data"]["confirmado_por_usuario"] == ["fecha_fin", "valor"]
    fila = next(f for f in _maestro(ctx) if f["id_contrato"] == "CM-2026-03")
    assert (fila["valor"], fila["fecha_fin"], fila["comercial"]) == ("0", "2027-08-31", "No registrado (jperez@periferia-ficticia.com)")


def test_registrar_es_idempotente_y_no_toca_el_fixture(ctx):
    huella = hashlib.sha256(ctx.rutas.maestro_fixture.read_bytes()).hexdigest()
    _procesar_buzon(ctx)
    filas = len(_maestro(ctx))
    otra_vez = llamar(registrar, {"mensaje_id": "msg-001"}, ctx)["data"]
    assert otra_vez["idempotente"] is True and otra_vez["id_contrato"] == "CT-2026-015"
    assert len(_maestro(ctx)) == filas
    assert hashlib.sha256(ctx.rutas.maestro_fixture.read_bytes()).hexdigest() == huella
    assert llamar(leer_buzon, {}, ctx)["data"]["pendientes"] == 1  # solo msg-006 sigue pendiente


def test_valor_propuesto_por_el_modelo_pasa_a_revision(ctx):
    v = llamar(validar, {"mensaje_id": "msg-001", "contrato": {"valor": 300_000_000}}, ctx)["data"]
    assert v["requiere_revision"] == ["valor"]
    assert "propuesto por el agente" in v["detalle_revision"][0]["motivo"]
    r = llamar(registrar, {"mensaje_id": "msg-001", "contrato": {"valor": 300_000_000}}, ctx)
    assert r["ok"] is False and r["error"] == "requiere revisión: valor"


def test_alertas(ctx):
    _procesar_buzon(ctx)
    data = llamar(alertas, {"hoy": HOY.isoformat()}, ctx)["data"]
    assert [f["id_contrato"] for f in data["vencen"]] == ["CT-2026-009", "CT-2026-004"]
    assert {f["id_contrato"] for f in data["polizas_pendientes"]} == {"CT-2026-004", "CT-2026-011", "CT-2026-015"}
    assert {f["id_contrato"] for f in data["registrados_desde_corte"]} == {"CT-2026-011", "CT-2026-015", "CT-2026-016"}
    contenido = ctx.rutas.alertas.read_text()
    assert "## 1. Vencen" in contenido and "## 2. Pólizas pendientes" in contenido and "## 3. Registrados" in contenido


def test_errores_legibles_sin_excepciones(ctx, proyecto):
    crear_mensaje(proyecto, "msg-vacio", "contrato.txt", "   ")
    crear_mensaje(proyecto, "msg-fecha", "contrato.txt", "CONTRATO No. CT-2026-090\n\nEntre ACME S.A.S., NIT 800.111.222-3\n\n"
                  "TERCERA. PLAZO. desde el treinta y uno (31) de febrero de 2026 hasta el uno (1) de marzo de 2027.\n")
    crear_mensaje(proyecto, "msg-moneda", "contrato.txt", "CONTRATO No. CT-2026-091\n\nSEGUNDA. VALOR. El valor es de MIL EUROS (EUR 1.000).\n")
    crear_mensaje(proyecto, "msg-sin-adjunto", None)
    assert llamar(extraer, {"mensaje_id": "msg-vacio"}, ctx) == {"ok": False, "error": "el adjunto 'contrato.txt' del mensaje msg-vacio está vacío"}
    assert "fecha inválida" in llamar(extraer, {"mensaje_id": "msg-fecha"}, ctx)["error"]
    assert "moneda desconocida" in llamar(extraer, {"mensaje_id": "msg-moneda"}, ctx)["error"]
    assert "no existe el mensaje" in llamar(extraer, {"mensaje_id": "msg-999"}, ctx)["error"]
    assert "argumentos inválidos" in llamar(extraer, {"mensaje_id": "../../etc"}, ctx)["error"]
    assert "argumentos inválidos" in llamar(alertas, {"hoy": "ayer"}, ctx)["error"]
    rechazo = llamar(validar, {"mensaje_id": "msg-sin-adjunto"}, ctx)["data"]
    assert rechazo["clasificacion"] == "rechazado" and "no trae adjuntos" in rechazo["motivo"]
    # un mensaje malo no bloquea el lote: el resto se sigue procesando
    assert llamar(registrar, {"mensaje_id": "msg-001"}, ctx)["ok"]
    log = [json.loads(l) for l in ctx.rutas.log.read_text().splitlines()]
    assert {"ts", "herramienta", "mensaje_id", "ok", "resumen"} <= set(log[0])


def test_otrosi_de_contrato_inexistente_se_rechaza(ctx, proyecto):
    crear_mensaje(proyecto, "msg-otrosi", "otrosi.txt", "OTROSÍ No. 1 AL CONTRATO No. CT-2099-001\n\nEntre ACME S.A.S., NIT 800.111.222-3, "
                  "y PERIFERIA IT GROUP S.A.S., NIT 900.123.456-7.\n\nPRIMERA. Modificar la cláusula TERCERA (PLAZO): hasta el "
                  "primero (1) de enero de 2028.\n")
    v = llamar(validar, {"mensaje_id": "msg-otrosi"}, ctx)["data"]
    assert v["clasificacion"] == "rechazado" and "no está en el maestro" in v["motivo"]


def test_leer_pdf_bloquea_rutas_fuera_del_proyecto(ctx):
    assert "fuera de fixtures" in llamar(leer_pdf, {"ruta": "../../etc/passwd"}, ctx)["error"]
    texto = llamar(leer_pdf, {"ruta": "fixtures/reto-02/buzon/msg-001/contrato.txt"}, ctx)["data"]["texto"]
    assert "CT-2026-015" in texto
