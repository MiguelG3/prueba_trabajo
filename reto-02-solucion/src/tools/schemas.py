"""Esquemas pydantic de las herramientas (equivalente a zod en el PRD).

Las `description` de cada campo SON parte del prompt: el modelo las lee en el JSON schema de
cada herramienta. Toda opción fija es `Literal` para que el schema exponga un `enum` y el modelo
no pueda inventar valores; pydantic además valida antes de ejecutar.
"""

from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

Pais = Literal["CO", "EC", "PE", "PA", "HN"]
Moneda = Literal["COP", "USD", "PEN", "PAB", "HNL"]
EstadoPoliza = Literal["vigente", "pendiente", "vencida", "no_aplica"]
Fuente = Literal["buzon", "manual", "migracion"]
Clasificacion = Literal["nuevo", "actualizacion", "duplicado", "rechazado"]
TipoDocumento = Literal["contrato", "contrato_marco", "otrosi", "otro"]
Accion = Literal["insertado", "actualizado", "sin_cambios", "rechazado"]
Origen = Literal["extraccion", "derivado", "modelo", "usuario"]

UMBRAL_CONFIANZA = 0.8
FECHA_CORTE_MAESTRO = date(2026, 5, 30)

MensajeId = Field(
    pattern=r"^[A-Za-z0-9_-]{1,64}$",
    description="Id de la carpeta del mensaje en el buzón (ej. 'msg-001'), tal como lo devuelve contratos_leer_buzon.",
)

T = TypeVar("T")


class CampoExtraido(BaseModel, Generic[T]):
    """Un valor extraído junto con la evidencia que lo respalda."""

    valor: T | None = Field(None, description="Valor normalizado. null si el documento no lo contiene.")
    confianza: float = Field(0.0, ge=0, le=1, description="Confianza en [0,1]. < 0.8 obliga a revisión humana.")
    evidencia: str | None = Field(None, description="Fragmento literal del documento del que sale el valor.")
    origen: Origen = Field("extraccion", description="De dónde sale el valor.")
    nota: str | None = Field(None, description="Explicación de cómo se obtuvo o por qué la confianza es baja.")


class ContratoExtraido(BaseModel):
    """Salida de contratos_extraer: esquema 7.2 del PRD con confianza por campo."""

    mensaje_id: str
    tipo_documento: TipoDocumento
    adjunto: str
    id_contrato: CampoExtraido[str]
    cliente: CampoExtraido[str]
    nit_cliente: CampoExtraido[str]
    pais: CampoExtraido[Pais]
    objeto: CampoExtraido[str]
    valor: CampoExtraido[float]
    valor_indeterminado: bool = False
    moneda: CampoExtraido[Moneda]
    fecha_inicio: CampoExtraido[date]
    fecha_fin: CampoExtraido[date]
    requiere_poliza: CampoExtraido[bool]
    tipo_poliza: CampoExtraido[str]
    estado_poliza: CampoExtraido[EstadoPoliza]
    advertencias: list[str] = Field(default_factory=list)

    def campos(self) -> dict[str, CampoExtraido]:
        return {nombre: getattr(self, nombre) for nombre in CAMPOS_CONTRATO}


CAMPOS_CONTRATO = (
    "id_contrato", "cliente", "nit_cliente", "pais", "objeto", "valor", "moneda",
    "fecha_inicio", "fecha_fin", "requiere_poliza", "tipo_poliza", "estado_poliza",
)


class ContratoPropuesto(BaseModel):
    """Valores que el agente propone o que el usuario confirmó. Todo campo omitido se toma de la extracción."""

    model_config = ConfigDict(extra="forbid")

    id_contrato: str | None = Field(None, description="Número del contrato tal como aparece en el documento (ej. 'CT-2026-015').")
    cliente: str | None = Field(None, description="Razón social de la contraparte (nunca Periferia IT Group).")
    nit_cliente: str | None = Field(None, description="Identificador tributario de la contraparte, solo dígitos, sin puntos ni dígito de verificación.")
    pais: Pais | None = Field(None, description="País de la contraparte, inferido del identificador (NIT→CO, RUC 13 díg.→EC, RUC 11 díg.→PE, RUC→PA, RTN→HN).")
    objeto: str | None = Field(None, max_length=200, description="Objeto del contrato, máximo 200 caracteres.")
    valor: float | None = Field(
        None, ge=0,
        description=(
            "Valor total sin separadores. 0 si el contrato es por demanda. Nunca estimes, redondees ni conviertas "
            "monedas: copia el valor de contratos_extraer o el que el usuario confirmó textualmente."
        ),
    )
    moneda: Moneda | None = Field(None, description="Moneda del valor del contrato.")
    fecha_inicio: date | None = Field(None, description="Fecha de inicio YYYY-MM-DD.")
    fecha_fin: date | None = Field(None, description="Fecha de terminación YYYY-MM-DD. Solo cámbiala si el usuario la confirmó.")
    requiere_poliza: bool | None = Field(None, description="true si el contrato exige constituir alguna póliza.")
    tipo_poliza: str | None = Field(None, description="Tipos de póliza separados por ';' (ej. 'cumplimiento;calidad'). Vacío si no requiere.")
    estado_poliza: EstadoPoliza | None = Field(None, description="Estado de la póliza. Un registro nuevo con póliza siempre es 'pendiente'.")

    @model_validator(mode="after")
    def _coherencia(self) -> "ContratoPropuesto":
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio")
        if self.requiere_poliza is False and self.tipo_poliza:
            raise ValueError("tipo_poliza debe ir vacío si requiere_poliza es false")
        return self

    def valores(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True)


class FilaMaestro(BaseModel):
    """Una fila de maestro-contratos.csv (sección 7.2). Se valida antes de escribir."""

    id_contrato: str = Field(min_length=1)
    cliente: str = Field(min_length=1)
    nit_cliente: str = Field(pattern=r"^\d+$")
    pais: Pais
    objeto: str = Field(min_length=1, max_length=200)
    valor: float = Field(ge=0)
    moneda: Moneda
    fecha_inicio: date
    fecha_fin: date
    requiere_poliza: bool
    tipo_poliza: str = ""
    estado_poliza: EstadoPoliza
    comercial: str = ""
    ruta_sharepoint: str = ""
    fecha_registro: date
    fuente: Fuente

    @model_validator(mode="after")
    def _coherencia(self) -> "FilaMaestro":
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio")
        return self


# ---------- argumentos de herramientas ----------


class LeerBuzonArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtraerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mensaje_id: str = MensajeId


class ValidarArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mensaje_id: str = MensajeId
    contrato: ContratoPropuesto | None = Field(
        None,
        description=(
            "Opcional. Solo los campos que quieras CORREGIR respecto a contratos_extraer; omite el resto. "
            "Todo valor distinto al extraído queda en revisión humana."
        ),
    )


class RegistrarArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mensaje_id: str = MensajeId
    contrato: ContratoPropuesto | None = Field(
        None,
        description=(
            "Opcional. Solo las correcciones que el usuario confirmó textualmente (ej. {'valor': 0, 'fecha_fin': '2027-08-31'}). "
            "Omite los campos que no cambian."
        ),
    )
    confirmado: bool = Field(
        False,
        description=(
            "true SOLO si el usuario confirmó explícitamente, en su último mensaje, los campos en revisión de este "
            "mensaje_id. Nunca lo pongas en true por iniciativa propia."
        ),
    )


class AlertasArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hoy: date = Field(description="Fecha de referencia YYYY-MM-DD que indicó el usuario. Si no la indicó, pregúntala; no la supongas.")


class LeerPdfArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ruta: str = Field(description="Ruta relativa al proyecto de un PDF o TXT dentro de fixtures/ u out/.")
