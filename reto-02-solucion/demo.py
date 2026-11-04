"""Verificación sin modelo de lenguaje: procesa el buzón llamando directamente a las herramientas.

    uv run demo.py

Determinista: limpia out/ al inicio y fija la fecha de referencia en 2026-09-03.
"""

import json
import shutil
from datetime import date
from pathlib import Path

from src.tools.base import Contexto
from src.tools.contratos import alertas, extraer, leer_buzon, registrar, validar

RAIZ = Path(__file__).resolve().parent
HOY = date(2026, 9, 3)
CORRECCION_MSG_006 = {"valor": 0, "fecha_fin": "2027-08-31"}


def llamar(tool, args: dict, ctx: Contexto) -> dict:
    return json.loads(tool.execute(args, ctx))


def fila(*columnas: object, anchos=(9, 14, 22, 58)) -> str:
    return "  ".join(str(c).ljust(a) for c, a in zip(columnas, anchos))


def main() -> None:
    shutil.rmtree(RAIZ / "out", ignore_errors=True)
    ctx = Contexto(directory=RAIZ, session_id="demo", hoy=HOY)

    buzon = llamar(leer_buzon, {}, ctx)["data"]
    print(f"\n📥 Buzón: {buzon['pendientes']} mensajes pendientes (hoy = {HOY})\n")
    print(fila("mensaje", "clasificación", "revisión", "acción"))
    print(fila("-" * 9, "-" * 14, "-" * 22, "-" * 58))

    for mensaje in buzon["mensajes"]:
        mid = mensaje["id"]
        extraido = llamar(extraer, {"mensaje_id": mid}, ctx)
        if not extraido["ok"]:
            print(fila(mid, "error", "-", extraido["error"]))
            continue
        validacion = llamar(validar, {"mensaje_id": mid}, ctx)["data"]
        resultado = llamar(registrar, {"mensaje_id": mid}, ctx)
        revision = ", ".join(validacion["requiere_revision"]) or "-"
        if resultado["ok"]:
            r = resultado["data"]
            accion = f"{r['accion']} {r['id_contrato'] or ''}".strip() + (f" — {r['motivo']}" if r.get("motivo") else "")
        else:
            accion = f"⏸ NO registrado: {resultado['error']}"
        print(fila(mid, validacion["clasificacion"], revision, accion))
        for aviso in (a for a in validacion["advertencias"] if a != validacion["motivo"]):
            print(fila("", "", "", f"⚠ {aviso[:110]}"))

    print(f"\n✅ Segunda pasada msg-006 con confirmado=true y correcciones del usuario {CORRECCION_MSG_006}")
    confirmado = llamar(registrar, {"mensaje_id": "msg-006", "contrato": CORRECCION_MSG_006, "confirmado": True}, ctx)
    print("   ", json.dumps({k: confirmado["data"][k] for k in ("accion", "id_contrato", "ruta_archivo", "confirmado_por_usuario")}, ensure_ascii=False))

    print("\n🔁 Idempotencia: registrar msg-001 otra vez")
    otra_vez = llamar(registrar, {"mensaje_id": "msg-001"}, ctx)["data"]
    print(f"    accion={otra_vez['accion']} id={otra_vez['id_contrato']} idempotente={otra_vez['idempotente']}")

    reporte = llamar(alertas, {"hoy": HOY.isoformat()}, ctx)["data"]
    print(f"\n🚨 Alertas ({reporte['ruta']})")
    print(f"    vencen ≤ 60 días     : {[f['id_contrato'] + ' (' + str(f['dias_restantes']) + 'd)' for f in reporte['vencen']]}")
    print(f"    pólizas pendientes   : {[f['id_contrato'] for f in reporte['polizas_pendientes']]}")
    print(f"    desde corte 05-30    : {[f['id_contrato'] + ' ' + f['accion'] for f in reporte['registrados_desde_corte']]}")
    print(f"    ya vencidos          : {[f['id_contrato'] for f in reporte['vencidos']]}")
    print("\nArchivos: out/sharepoint/maestro-contratos.csv · out/sharepoint/historial.jsonl · out/procesados.json · out/log.jsonl\n")


if __name__ == "__main__":
    main()
