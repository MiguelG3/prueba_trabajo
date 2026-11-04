"""Validación y clasificación RN1–RN5 de un contrato extraído contra el maestro."""

from dataclasses import dataclass, field

from src.dominio.texto import similitud
from src.tools.schemas import (
    UMBRAL_CONFIANZA,
    CampoExtraido,
    Clasificacion,
    ContratoExtraido,
    FilaMaestro,
)

UMBRAL_SIMILITUD_OBJETO = 0.9
CONFIANZA_PROPUESTA_MODELO = 0.79  # por debajo del umbral: todo valor del modelo pasa por un humano
CAMPOS_DUPLICADO = ("valor", "fecha_inicio", "fecha_fin")


@dataclass
class Validacion:
    clasificacion: Clasificacion
    campos: dict[str, CampoExtraido]
    requiere_revision: list[str] = field(default_factory=list)
    detalle_revision: list[dict] = field(default_factory=list)
    id_contrato_existente: str | None = None
    diferencias: dict[str, dict] = field(default_factory=dict)
    motivo: str | None = None
    comercial: str = ""
    advertencias: list[str] = field(default_factory=list)

    def valores(self) -> dict[str, object]:
        return {k: c.valor for k, c in self.campos.items()}


def aplicar_propuesta(campos: dict[str, CampoExtraido], propuesta: dict[str, object], origen: str) -> None:
    """Sustituye valores propuestos (modelo o usuario). Los del modelo bajan a confianza 0.79 → revisión."""
    for nombre, valor in propuesta.items():
        actual = campos[nombre]
        if valor == actual.valor:
            continue
        confianza = 1.0 if origen == "usuario" else CONFIANZA_PROPUESTA_MODELO
        nota = f"{'confirmado por el usuario' if origen == 'usuario' else 'propuesto por el agente'}; valor extraído: {actual.valor!r}"
        campos[nombre] = CampoExtraido(valor=valor, confianza=confianza, evidencia=actual.evidencia, origen=origen, nota=nota)


def resolver_comercial(remitente: str, comerciales: list[dict]) -> tuple[str, str | None]:
    """(nombre para el maestro, advertencia si el remitente no está en comerciales.json)."""
    encontrado = next((c for c in comerciales if c["email"].lower() == remitente.lower()), None)
    if encontrado:
        return encontrado["nombre"], None
    return f"No registrado ({remitente})", f"remitente {remitente} no está en comerciales.json: se registra igual, pero hay que asignar comercial"


def _diferencias(campos: dict[str, CampoExtraido], existente: FilaMaestro) -> dict[str, dict]:
    actual = existente.model_dump()
    return {
        nombre: {"antes": actual[nombre], "despues": campo.valor}
        for nombre, campo in campos.items()
        if campo.valor is not None and nombre in actual and campo.valor != actual[nombre]
    }


def _revision(campos: dict[str, CampoExtraido], nombres: list[str]) -> list[dict]:
    detalle = []
    for nombre in nombres:
        campo = campos[nombre]
        if nombre == "id_contrato" and campo.valor is None:
            continue  # se asigna AUTO-<año>-<seq> por regla, no requiere revisión
        if campo.confianza < UMBRAL_CONFIANZA:
            detalle.append({"campo": nombre, "valor": campo.valor, "confianza": campo.confianza, "motivo": campo.nota or "confianza baja"})
    return detalle


def _rechazo(extraido: ContratoExtraido, campos: dict, motivo: str) -> Validacion:
    return Validacion("rechazado", campos, motivo=motivo, advertencias=list(extraido.advertencias))


def validar_contrato(
    extraido: ContratoExtraido,
    maestro: list[FilaMaestro],
    remitente: str,
    comerciales: list[dict],
    propuesta: dict[str, object] | None = None,
    origen_propuesta: str = "modelo",
) -> Validacion:
    campos = {k: v.model_copy() for k, v in extraido.campos().items()}
    if extraido.tipo_documento == "otro":
        return _rechazo(extraido, campos, extraido.advertencias[0] if extraido.advertencias else "el mensaje no trae un contrato")
    aplicar_propuesta(campos, propuesta or {}, origen_propuesta)
    if campos["cliente"].valor is None and campos["objeto"].valor is None:
        return _rechazo(extraido, campos, "el texto no contiene partes ni objeto identificables")

    comercial, aviso = resolver_comercial(remitente, comerciales)
    advertencias = list(extraido.advertencias) + ([aviso] if aviso else [])
    existente = next((f for f in maestro if campos["id_contrato"].valor and f.id_contrato == campos["id_contrato"].valor), None)

    if extraido.tipo_documento == "otrosi":
        if not existente:
            return _rechazo(extraido, campos, f"otrosí de un contrato que no está en el maestro ({campos['id_contrato'].valor}); registrar primero el contrato base")
        diferencias = _diferencias(campos, existente)
        detalle = _revision(campos, list(diferencias))
        return Validacion("actualizacion", campos, [d["campo"] for d in detalle], detalle, existente.id_contrato, diferencias,
                          comercial=comercial, advertencias=advertencias)

    if existente:
        diferencias = _diferencias(campos, existente)
        if not any(c in diferencias for c in CAMPOS_DUPLICADO):
            return Validacion("duplicado", campos, id_contrato_existente=existente.id_contrato, diferencias=diferencias,
                              motivo=f"ya existe {existente.id_contrato} con el mismo valor y vigencia", comercial=comercial,
                              advertencias=advertencias)
        # Mismo número pero datos distintos sin ser otrosí: es un conflicto con el maestro → revisión humana.
        detalle = [{"campo": c, "valor": d["despues"], "confianza": campos[c].confianza,
                    "motivo": f"conflicto con el maestro: antes {d['antes']!r}"} for c, d in diferencias.items()]
        return Validacion("actualizacion", campos, list(diferencias), detalle, existente.id_contrato, diferencias,
                          comercial=comercial, advertencias=advertencias)

    similar = next((f for f in maestro if f.nit_cliente == campos["nit_cliente"].valor and campos["objeto"].valor
                    and similitud(f.objeto, str(campos["objeto"].valor)) >= UMBRAL_SIMILITUD_OBJETO), None)
    if similar:
        diferencias = _diferencias({k: v for k, v in campos.items() if k != "id_contrato"}, similar)
        detalle = _revision(campos, list(diferencias))
        advertencias.append(f"coincide por NIT + objeto con {similar.id_contrato} (similitud ≥ {UMBRAL_SIMILITUD_OBJETO})")
        return Validacion("actualizacion", campos, [d["campo"] for d in detalle], detalle, similar.id_contrato, diferencias,
                          comercial=comercial, advertencias=advertencias)

    detalle = _revision(campos, list(campos))
    return Validacion("nuevo", campos, [d["campo"] for d in detalle], detalle, comercial=comercial, advertencias=advertencias)
