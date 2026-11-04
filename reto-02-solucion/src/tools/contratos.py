"""Herramientas del agente de contratos. Cada export es una `Tool`; el modelo la ve como `contratos_<export>`.

Son la ÚNICA fuente de valores que el agente puede afirmar. La lógica de negocio vive en src/dominio/.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from src.dominio import maestro as repo
from src.dominio.clasificacion import Validacion, validar_contrato
from src.dominio.extraccion import detectar_tipo, extraer_contrato, sin_contrato
from src.dominio.texto import slug_cliente
from src.tools.base import Contexto, Tool, ToolError
from src.tools.schemas import (
    FECHA_CORTE_MAESTRO,
    AlertasArgs,
    ContratoExtraido,
    ExtraerArgs,
    FilaMaestro,
    LeerBuzonArgs,
    LeerPdfArgs,
    RegistrarArgs,
    ValidarArgs,
)

PREFIJOS_CONTRATO = ("contrato", "otrosi", "otrosí", "acta")
DIAS_ALERTA_VENCIMIENTO = 60
OBLIGATORIOS = ("cliente", "nit_cliente", "pais", "objeto", "valor", "moneda", "fecha_inicio", "fecha_fin", "requiere_poliza")


# ---------- utilidades de lectura ----------


def _leer_texto(ruta: Path) -> str:
    if ruta.suffix.lower() == ".pdf":
        from pypdf import PdfReader  # import perezoso: solo se necesita para PDF nativos (P1)

        return "\n".join(p.extract_text() or "" for p in PdfReader(ruta).pages)
    return ruta.read_text(encoding="utf-8")


def _adjunto_principal(ctx: Contexto, mensaje_id: str, correo: dict) -> Path | None:
    """El adjunto que parece contrato (por nombre); si ninguno, el primero que exista."""
    carpeta = ctx.rutas.buzon / mensaje_id
    existentes = [carpeta / a for a in correo.get("adjuntos", []) if (carpeta / a).is_file()]
    preferido = next((p for p in existentes if p.stem.lower().startswith(PREFIJOS_CONTRATO)), None)
    return preferido or (existentes[0] if existentes else None)


def _fecha_correo(correo: dict) -> date | None:
    try:
        return date.fromisoformat(str(correo.get("fecha", ""))[:10])
    except ValueError:
        return None


def _extraer(ctx: Contexto, mensaje_id: str) -> tuple[dict, ContratoExtraido, Path | None]:
    correo = repo.leer_correo(ctx.rutas, mensaje_id)
    adjunto = _adjunto_principal(ctx, mensaje_id, correo)
    if adjunto is None:
        return correo, sin_contrato(mensaje_id, "el correo no trae adjuntos"), None
    return correo, extraer_contrato(mensaje_id, adjunto.name, _leer_texto(adjunto), _fecha_correo(correo)), adjunto


def _comerciales(ctx: Contexto) -> list[dict]:
    return json.loads(ctx.rutas.comerciales.read_text(encoding="utf-8"))


def _validar(ctx: Contexto, mensaje_id: str, propuesta: dict, origen: str) -> tuple[dict, ContratoExtraido, Path | None, Validacion]:
    correo, extraido, adjunto = _extraer(ctx, mensaje_id)
    validacion = validar_contrato(extraido, repo.leer_maestro(ctx.rutas), correo.get("de", ""), _comerciales(ctx), propuesta, origen)
    return correo, extraido, adjunto, validacion


def _json(valor: object) -> object:
    return valor.isoformat() if isinstance(valor, date) else valor


# ---------- contratos_leer_buzon ----------


def _run_leer_buzon(_: LeerBuzonArgs, ctx: Contexto) -> dict:
    procesados = repo.leer_procesados(ctx.rutas)
    mensajes = []
    for mensaje_id in repo.listar_mensajes(ctx.rutas):
        if mensaje_id in procesados:
            continue
        try:
            correo = repo.leer_correo(ctx.rutas, mensaje_id)
        except (FileNotFoundError, ValueError) as exc:
            mensajes.append({"id": mensaje_id, "error": str(exc), "tiene_contrato": False})
            continue
        adjunto = _adjunto_principal(ctx, mensaje_id, correo)
        tipo = detectar_tipo(_leer_texto(adjunto)) if adjunto else "otro"
        mensajes.append({
            "id": mensaje_id, "de": correo.get("de"), "asunto": correo.get("asunto"), "fecha": correo.get("fecha"),
            "adjuntos": correo.get("adjuntos", []), "tiene_contrato": tipo != "otro", "tipo_documento": tipo,
        })
    return {"mensajes": mensajes, "pendientes": len(mensajes), "ya_procesados": len(procesados)}


leer_buzon = Tool(
    nombre="contratos_leer_buzon",
    description="Lista los mensajes del buzón de contratos aún no procesados, indicando si cada uno trae un contrato u otrosí.",
    args=LeerBuzonArgs,
    run=_run_leer_buzon,
    resumir=lambda d: f"{d['pendientes']} mensajes pendientes, {d['ya_procesados']} ya procesados",
)


# ---------- contratos_extraer ----------


def _run_extraer(args: ExtraerArgs, ctx: Contexto) -> dict:
    _, extraido, _ = _extraer(ctx, args.mensaje_id)
    return extraido.model_dump(mode="json")


extraer = Tool(
    nombre="contratos_extraer",
    description="Extrae del adjunto de un mensaje los datos del contrato (esquema del maestro) con confianza y evidencia por campo.",
    args=ExtraerArgs,
    run=_run_extraer,
    resumir=lambda d: f"{d['tipo_documento']} {d['id_contrato']['valor'] or ''}".strip(),
)


# ---------- contratos_validar ----------


def _salida_validacion(v: Validacion) -> dict:
    return {
        "clasificacion": v.clasificacion,
        "id_contrato_existente": v.id_contrato_existente,
        "requiere_revision": v.requiere_revision,
        "detalle_revision": [{**d, "valor": _json(d["valor"])} for d in v.detalle_revision],
        "diferencias": {k: {"antes": _json(d["antes"]), "despues": _json(d["despues"])} for k, d in v.diferencias.items()},
        "motivo": v.motivo,
        "comercial": v.comercial,
        "advertencias": v.advertencias,
        "contrato": {k: _json(val) for k, val in v.valores().items()},
    }


def _run_validar(args: ValidarArgs, ctx: Contexto) -> dict:
    propuesta = args.contrato.valores() if args.contrato else {}
    *_, validacion = _validar(ctx, args.mensaje_id, propuesta, "modelo")
    return _salida_validacion(validacion)


validar = Tool(
    nombre="contratos_validar",
    description="Clasifica el contrato de un mensaje como nuevo, actualizacion, duplicado o rechazado y lista los campos que requieren revisión humana.",
    args=ValidarArgs,
    run=_run_validar,
    resumir=lambda d: f"{d['clasificacion']}" + (f" · revisión: {', '.join(d['requiere_revision'])}" if d["requiere_revision"] else ""),
)


# ---------- contratos_registrar ----------


def _fila_nueva(v: Validacion, maestro: list[FilaMaestro], ctx: Contexto) -> FilaMaestro:
    valores = v.valores()
    faltantes = [c for c in OBLIGATORIOS if valores.get(c) is None]
    if faltantes:
        raise ToolError(f"faltan campos obligatorios para registrar: {', '.join(faltantes)}", {"faltantes": faltantes})
    requiere = bool(valores["requiere_poliza"])
    return FilaMaestro(
        id_contrato=valores["id_contrato"] or repo.siguiente_id_auto(maestro, valores["fecha_inicio"].year),
        cliente=valores["cliente"], nit_cliente=valores["nit_cliente"], pais=valores["pais"], objeto=valores["objeto"],
        valor=valores["valor"], moneda=valores["moneda"], fecha_inicio=valores["fecha_inicio"], fecha_fin=valores["fecha_fin"],
        requiere_poliza=requiere, tipo_poliza=(valores["tipo_poliza"] or "") if requiere else "",
        estado_poliza=(valores["estado_poliza"] or "pendiente") if requiere else "no_aplica",
        comercial=v.comercial, fecha_registro=ctx.hoy, fuente="buzon",
    )


def _insertar(v: Validacion, maestro: list[FilaMaestro], adjunto: Path, ctx: Contexto, mensaje_id: str) -> dict:
    fila = _fila_nueva(v, maestro, ctx)
    ruta = repo.archivar(ctx.rutas, adjunto, fila.fecha_inicio.year, slug_cliente(fila.cliente), fila.id_contrato)
    fila = fila.model_copy(update={"ruta_sharepoint": ruta})
    repo.guardar_maestro(ctx.rutas, [*maestro, fila])
    repo.agregar_jsonl(ctx.rutas.historial, {"ts": repo.ahora_iso(), "id_contrato": fila.id_contrato, "accion": "insertado",
                                             "cambios": fila.model_dump(mode="json"), "mensaje_id": mensaje_id})
    return {"id_contrato": fila.id_contrato, "accion": "insertado", "ruta_archivo": f"out/sharepoint/{ruta}"}


def _actualizar(v: Validacion, maestro: list[FilaMaestro], adjunto: Path, ctx: Contexto, mensaje_id: str, tipo: str) -> dict:
    existente = repo.buscar_por_id(maestro, v.id_contrato_existente)
    cambios = {k: d["despues"] for k, d in v.diferencias.items() if k != "id_contrato"}
    actualizada = FilaMaestro(**{**existente.model_dump(), **cambios})
    sufijo = f"{tipo}-{mensaje_id}" if tipo == "otrosi" else f"version-{mensaje_id}"
    ruta = repo.archivar(ctx.rutas, adjunto, existente.fecha_inicio.year, slug_cliente(existente.cliente), f"{existente.id_contrato}_{sufijo}")
    repo.guardar_maestro(ctx.rutas, [actualizada if f.id_contrato == existente.id_contrato else f for f in maestro])
    diferencias = {k: {"antes": _json(d["antes"]), "despues": _json(d["despues"])} for k, d in v.diferencias.items() if k in cambios}
    repo.agregar_jsonl(ctx.rutas.historial, {"ts": repo.ahora_iso(), "id_contrato": existente.id_contrato, "accion": "actualizado",
                                             "cambios": diferencias, "mensaje_id": mensaje_id, "documento": f"out/sharepoint/{ruta}"})
    return {"id_contrato": existente.id_contrato, "accion": "actualizado", "ruta_archivo": f"out/sharepoint/{ruta}", "cambios": diferencias}


def _run_registrar(args: RegistrarArgs, ctx: Contexto) -> dict:
    with repo.LOCK:
        previo = repo.leer_procesados(ctx.rutas).get(args.mensaje_id)
        if previo:
            return {**previo, "idempotente": True}
        propuesta = args.contrato.valores() if args.contrato else {}
        _, extraido, adjunto, v = _validar(ctx, args.mensaje_id, propuesta, "usuario" if args.confirmado else "modelo")
        if v.requiere_revision and not args.confirmado:
            raise ToolError(f"requiere revisión: {', '.join(v.requiere_revision)}", {"detalle_revision": _salida_validacion(v)["detalle_revision"]})
        repo.asegurar_maestro(ctx.rutas)
        maestro = repo.leer_maestro(ctx.rutas)
        if v.clasificacion == "nuevo":
            resultado = _insertar(v, maestro, adjunto, ctx, args.mensaje_id)
        elif v.clasificacion == "actualizacion":
            resultado = _actualizar(v, maestro, adjunto, ctx, args.mensaje_id, extraido.tipo_documento)
        elif v.clasificacion == "duplicado":
            resultado = {"id_contrato": v.id_contrato_existente, "accion": "sin_cambios", "ruta_archivo": None, "motivo": v.motivo}
        else:
            resultado = {"id_contrato": None, "accion": "rechazado", "ruta_archivo": None, "motivo": v.motivo}
        confirmados = sorted(set(v.requiere_revision) | {k for k, c in v.campos.items() if c.origen == "usuario"}) if args.confirmado else []
        resultado |= {"clasificacion": v.clasificacion, "confirmado_por_usuario": confirmados, "advertencias": v.advertencias}
        repo.marcar_procesado(ctx.rutas, args.mensaje_id, {**resultado, "ts": repo.ahora_iso()})
        return {**resultado, "idempotente": False}


registrar = Tool(
    nombre="contratos_registrar",
    description="Cierra un mensaje: inserta o actualiza el contrato en el maestro y lo archiva; si hay campos en revisión exige confirmado=true.",
    args=RegistrarArgs,
    run=_run_registrar,
    resumir=lambda d: f"{d['accion']} {d.get('id_contrato') or ''}".strip() + (" (idempotente)" if d.get("idempotente") else ""),
)


# ---------- contratos_alertas ----------


def _fila_resumen(f: FilaMaestro, **extra: object) -> dict:
    return {"id_contrato": f.id_contrato, "cliente": f.cliente, "fecha_fin": f.fecha_fin.isoformat(),
            "requiere_poliza": f.requiere_poliza, "tipo_poliza": f.tipo_poliza, "estado_poliza": f.estado_poliza,
            "comercial": f.comercial, **extra}


def _tabla(filas: list[dict], columnas: list[str]) -> str:
    if not filas:
        return "_Sin registros._\n"
    encabezado = "| " + " | ".join(columnas) + " |\n|" + "---|" * len(columnas) + "\n"
    return encabezado + "".join("| " + " | ".join(str(f.get(c, "")) for c in columnas) + " |\n" for f in filas)


def _markdown_alertas(hoy: date, vencen: list, polizas: list, desde_corte: list, vencidos: list) -> str:
    return (
        f"# Alertas de contratos — {hoy.isoformat()}\n\n"
        f"## 1. Vencen en los próximos {DIAS_ALERTA_VENCIMIENTO} días\n\n"
        + _tabla(vencen, ["id_contrato", "cliente", "fecha_fin", "dias_restantes", "comercial"])
        + "\n## 2. Pólizas pendientes (requiere_poliza = true y estado ≠ vigente)\n\n"
        + _tabla(polizas, ["id_contrato", "cliente", "tipo_poliza", "estado_poliza", "fecha_fin"])
        + f"\n## 3. Registrados o actualizados desde el corte ({FECHA_CORTE_MAESTRO.isoformat()}) — gap cubierto\n\n"
        + _tabla(desde_corte, ["id_contrato", "cliente", "accion", "fecha_fin", "comercial"])
        + "\n## Anexo. Vencidos sin acta de terminación ni otrosí registrado\n\n"
        + _tabla(vencidos, ["id_contrato", "cliente", "fecha_fin", "dias_vencido", "comercial"])
    )


def _run_alertas(args: AlertasArgs, ctx: Contexto) -> dict:
    filas = repo.leer_maestro(ctx.rutas)
    limite = args.hoy + timedelta(days=DIAS_ALERTA_VENCIMIENTO)
    vencen = [_fila_resumen(f, dias_restantes=(f.fecha_fin - args.hoy).days) for f in sorted(filas, key=lambda f: f.fecha_fin)
              if args.hoy <= f.fecha_fin <= limite]
    vencidos = [_fila_resumen(f, dias_vencido=(args.hoy - f.fecha_fin).days) for f in filas if f.fecha_fin < args.hoy]
    polizas = [_fila_resumen(f) for f in filas if f.requiere_poliza and f.estado_poliza != "vigente"]
    acciones = {h["id_contrato"]: h["accion"] for h in repo.leer_jsonl(ctx.rutas.historial)}
    desde_corte = [_fila_resumen(f, accion=acciones.get(f.id_contrato, "registrado")) for f in filas
                   if f.id_contrato in acciones or f.fecha_registro > FECHA_CORTE_MAESTRO]
    repo.escribir_atomico(ctx.rutas.alertas, _markdown_alertas(args.hoy, vencen, polizas, desde_corte, vencidos))
    return {"ruta": "out/alertas.md", "hoy": args.hoy.isoformat(), "vencen": vencen, "polizas_pendientes": polizas,
            "registrados_desde_corte": desde_corte, "vencidos": vencidos}


alertas = Tool(
    nombre="contratos_alertas",
    description="Genera out/alertas.md con contratos que vencen en ≤ 60 días, pólizas pendientes y contratos registrados desde el corte del maestro.",
    args=AlertasArgs,
    run=_run_alertas,
    resumir=lambda d: f"{len(d['vencen'])} vencen, {len(d['polizas_pendientes'])} pólizas pendientes, {len(d['registrados_desde_corte'])} desde corte",
)


# ---------- contratos_leer_pdf (P1) ----------


def _run_leer_pdf(args: LeerPdfArgs, ctx: Contexto) -> dict:
    ruta = ctx.rutas.dentro_de_proyecto(args.ruta)
    if not ruta.is_file():
        raise FileNotFoundError(f"no existe el archivo '{args.ruta}'")
    texto = _leer_texto(ruta)
    if not texto.strip():
        raise ToolError("el documento no tiene texto extraíble (¿PDF escaneado? requiere OCR, fuera de alcance)")
    return {"texto": texto, "caracteres": len(texto)}


leer_pdf = Tool(
    nombre="contratos_leer_pdf",
    description="Lee el texto de un PDF nativo o TXT ubicado dentro de fixtures/ u out/.",
    args=LeerPdfArgs,
    run=_run_leer_pdf,
    resumir=lambda d: f"{d['caracteres']} caracteres",
)


HERRAMIENTAS: dict[str, Tool] = {t.nombre: t for t in (leer_buzon, extraer, validar, registrar, alertas, leer_pdf)}
