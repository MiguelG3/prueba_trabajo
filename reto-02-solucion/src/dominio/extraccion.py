"""Extracción determinista (P0) de contratos en texto: regex + heurísticas con confianza por campo.

Tabla de confianza (documentada en SOLUCION.md):
  0.95  valor explícito y verificado por una segunda fuente (número = letras, rango = plazo)
  0.90  valor explícito con una sola fuente
  0.85  valor explícito pero con fuente débil (moneda por palabra, ausencia de cláusula de póliza)
  0.80  valor derivado por regla de negocio con buena evidencia (moneda por país, firma acotada por correo)
  0.60  valor derivado por cálculo (fecha_fin = inicio + plazo)  → revisión humana
  0.50  valor indeterminado o inconsistente                       → revisión humana
  0.00  ausente
"""

import re
from datetime import date

from src.dominio.fechas import buscar_solo_mes, fin_por_plazo, primera_fecha
from src.dominio.numeros import palabras_a_numero, parsear_monto
from src.dominio.texto import colapsar_espacios, sin_tildes
from src.tools.schemas import CampoExtraido, ContratoExtraido, TipoDocumento

NIT_PERIFERIA = "900123456"
MONEDAS = {"COP", "USD", "PEN", "PAB", "HNL"}
MONEDA_POR_PALABRA = {"pesos": "COP", "dolares": "USD", "soles": "PEN", "balboas": "PAB", "lempiras": "HNL"}
MONEDA_POR_PAIS = {"CO": "COP", "EC": "USD", "PE": "PEN", "PA": "USD", "HN": "HNL"}
PAIS_POR_NOMBRE = {"colombia": "CO", "ecuador": "EC", "peru": "PE", "panama": "PA", "honduras": "HN"}

_ID = re.compile(r"No\.\s*([A-Z]{1,5}-\d{4}-\d+)")
_PARTE = re.compile(
    r"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9.&\- ]*?[A-ZÁÉÍÓÚÑ.])\s*,?\s+(?:identificad[ao]\s+con\s+)?(NIT|RUC|RTN)\s+([\d][\d.\-]*\d)"
)
_CLAUSULA = re.compile(
    r"^(PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|S[ÉE]PTIMA|OCTAVA|NOVENA|D[ÉE]CIMA)\.\s+(?:([A-ZÁÉÍÓÚÑ ]+)\.\s+)?(.+)$",
    re.MULTILINE,
)
_MONTO = re.compile(r"\(\s*([A-Z]{3})\s*\$?\s*([\d][\d.,]*)\s*\)")
_LETRAS_ANTES_DE_MONTO = re.compile(r"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ /]+?)\s*\(\s*[A-Z]{3}\s*\$?\s*[\d]")
_INDETERMINADO = re.compile(r"no tiene un valor determinado|valor indeterminado|por demanda|cuant[ií]a indeterminada", re.I)
_PLAZO_MESES = re.compile(r"\((\d{1,3})\)\s+mes(?:es)?", re.I)
_RANGO = re.compile(r"desde\s+el\s+(.+?)\s+hasta\s+el\s+(.+?)(?:\.|$)", re.I | re.S)
_POLIZA_TIPO = re.compile(r"p[oó]lizas?\s+de\s+([a-záéíóúñ ]+?)(?=\s+por\b|\s+con\b|\s+a\s+favor|\s+expedida|,|\.|$)", re.I)
_CONDICIONAL = re.compile(r"para cada orden|cuyo valor supere|en caso de que|cuando el valor", re.I)
_AMPLIAR_GARANTIAS = re.compile(r"(garant[ií]as|p[oó]liza).{0,200}(ampliar|prorrogar|actualizar)", re.I | re.S)
_BOILERPLATE_OBJETO = re.compile(
    r"^(?:Establecer las condiciones generales bajo las cuales\s+)?EL CONTRATISTA\s+(?:se obliga a|prestar[áa]|ejecutar[áa])\s+(?:el servicio de\s+)?",
    re.I,
)


class ExtraccionError(ValueError):
    """Error legible para el usuario (texto vacío, moneda desconocida...)."""


def _campo(valor, confianza: float, evidencia: str | None = None, origen: str = "extraccion", nota: str | None = None) -> CampoExtraido:
    return CampoExtraido(valor=valor, confianza=confianza, evidencia=evidencia, origen=origen, nota=nota)


def _vacio(nota: str | None = None) -> CampoExtraido:
    return CampoExtraido(nota=nota)


def detectar_tipo(texto: str) -> TipoDocumento:
    cabecera = sin_tildes(next((l for l in texto.splitlines() if l.strip()), "").upper())
    if cabecera.startswith("OTROSI"):
        return "otrosi"
    if "CONTRATO MARCO" in cabecera:
        return "contrato_marco"
    if cabecera.startswith("CONTRATO"):
        return "contrato"
    return "otro"


def describir_documento(texto: str) -> str:
    """Nombre legible del tipo de documento para motivos de rechazo ('cotización', 'acta'...)."""
    cabecera = next((l.strip() for l in texto.splitlines() if l.strip()), "")
    return cabecera.split(" No.")[0].split(" No ")[0].lower() or "documento vacío"


def _clausulas(texto: str) -> list[tuple[str, str]]:
    """[(titulo, cuerpo)] — el título es '' en cláusulas sin título (como en los otrosíes)."""
    return [((m.group(2) or "").strip().upper(), m.group(3).strip()) for m in _CLAUSULA.finditer(texto)]


def _clausula(clausulas: list[tuple[str, str]], *palabras: str) -> str | None:
    for titulo, cuerpo in clausulas:
        if any(p in sin_tildes(titulo) for p in palabras):
            return cuerpo
    return None


def _nombre_legible(nombre_mayusculas: str, texto: str) -> str:
    """Busca en el documento la misma razón social escrita en minúsculas (bloque de firmas)."""
    for m in re.finditer(re.escape(nombre_mayusculas), texto, re.I):
        if m.group(0) != nombre_mayusculas.upper():
            return m.group(0)
    return nombre_mayusculas.title()


def _pais_desde_identificador(tipo: str, numero: str) -> tuple[str | None, float]:
    if tipo == "NIT":
        return "CO", 0.9
    if tipo == "RTN":
        return "HN", 0.9
    if len(numero) == 13:
        return "EC", 0.9
    if len(numero) == 11:
        return "PE", 0.9
    return "PA", 0.75


def _extraer_parte(texto: str) -> tuple[CampoExtraido, CampoExtraido, CampoExtraido]:
    for m in _PARTE.finditer(texto):
        tipo, crudo = m.group(2), m.group(3)
        numero = re.sub(r"\D", "", crudo.split("-")[0] if tipo == "NIT" else crudo)
        if numero == NIT_PERIFERIA:
            continue
        evidencia = colapsar_espacios(m.group(0))
        cliente = _campo(_nombre_legible(m.group(1).strip(" ,"), texto), 0.9, evidencia)
        nit = _campo(numero, 0.95, evidencia, nota=f"{tipo} sin puntos ni dígito de verificación" if tipo == "NIT" else tipo)
        pais, conf = _pais_desde_identificador(tipo, numero)
        domicilio = re.search(r"domicilio en ([^,]+),\s*([A-Za-zÁÉÍÓÚáéíóúñ]+)", texto[m.end(): m.end() + 120])
        nota = f"inferido del {tipo} ({len(numero)} dígitos)"
        if domicilio and PAIS_POR_NOMBRE.get(sin_tildes(domicilio.group(2).lower())) == pais:
            conf, nota = 0.95, nota + f" y confirmado por el domicilio ({domicilio.group(0)})"
        return cliente, nit, _campo(pais, conf, evidencia, nota=nota)
    nota = "no se identificó la contraparte con NIT/RUC/RTN"
    return _vacio(nota), _vacio(nota), _vacio(nota)


def _extraer_valor(cuerpo: str | None) -> tuple[CampoExtraido, CampoExtraido, bool]:
    """(valor, moneda, valor_indeterminado)."""
    if not cuerpo:
        return _vacio("no se encontró la cláusula de valor"), _vacio(), False
    if _INDETERMINADO.search(cuerpo):
        nota = "contrato sin valor determinado (por demanda): se registra 0 y requiere confirmación"
        return _campo(0.0, 0.5, colapsar_espacios(cuerpo[:160]), "derivado", nota), _vacio(), True
    monto = _MONTO.search(cuerpo)
    if not monto:
        return _vacio("la cláusula de valor no trae un monto numérico"), _vacio(), False
    codigo = monto.group(1)
    if codigo not in MONEDAS:
        raise ExtraccionError(f"moneda desconocida '{codigo}' en '{monto.group(0)}'. Monedas válidas: {', '.join(sorted(MONEDAS))}")
    numero = parsear_monto(monto.group(2))
    letras = _LETRAS_ANTES_DE_MONTO.search(cuerpo)
    en_letras = palabras_a_numero(letras.group(1)) if letras else None
    evidencia = colapsar_espacios((letras.group(1) if letras else "") + " " + monto.group(0))
    if en_letras is None:
        valor = _campo(numero, 0.85, evidencia, nota="solo hay valor en números")
    elif en_letras == numero:
        valor = _campo(numero, 0.95, evidencia, nota="valor en números coincide con el valor en letras")
    else:
        valor = _campo(numero, 0.5, evidencia, nota=f"el valor en letras ({en_letras:,.0f}) no coincide con el valor en números ({numero:,.0f})")
    return valor, _campo(codigo, 0.95, monto.group(0)), False


def _moneda_por_palabra(cuerpo: str | None) -> CampoExtraido:
    for palabra, codigo in MONEDA_POR_PALABRA.items():
        if cuerpo and palabra in sin_tildes(cuerpo.lower()):
            return _campo(codigo, 0.85, palabra, nota="moneda inferida por palabra")
    return _vacio("no se encontró la moneda")


def _fecha_firma(texto: str, fecha_correo: date | None) -> CampoExtraido:
    inicio = re.search(r"se\s+firma", texto, re.I)
    if not inicio:
        return _vacio("no se encontró la fecha de firma")
    segmento = texto[inicio.start(): inicio.start() + 250]
    completa = primera_fecha(segmento)
    if completa:
        return _campo(completa[0], 0.9, completa[1], nota="fecha de firma")
    solo_mes = buscar_solo_mes(segmento)
    if not solo_mes:
        return _vacio("la firma no indica fecha")
    anio, mes, evidencia = solo_mes
    if fecha_correo and (fecha_correo.year, fecha_correo.month) == (anio, mes):
        nota = "el documento solo indica mes y año de firma; se acota con la fecha del correo (la firma no puede ser posterior al envío)"
        return _campo(fecha_correo, 0.8, evidencia, "derivado", nota)
    return _campo(date(anio, mes, 1), 0.5, evidencia, "derivado", "el documento solo indica mes y año de firma; se asume el día 1")


def _extraer_fechas(cuerpo: str | None, texto: str, fecha_correo: date | None) -> tuple[CampoExtraido, CampoExtraido]:
    if not cuerpo:
        return _vacio("no se encontró la cláusula de plazo"), _vacio("no se encontró la cláusula de plazo")
    meses = _PLAZO_MESES.search(cuerpo)
    rango = _RANGO.search(cuerpo)
    if rango:
        desde, hasta = primera_fecha(rango.group(1)), primera_fecha(rango.group(2))
        if desde and hasta:
            inicio = _campo(desde[0], 0.95, desde[1])
            fin = _campo(hasta[0], 0.95, hasta[1], nota="rango explícito")
            if meses and fin_por_plazo(desde[0], int(meses.group(1))) != hasta[0]:
                fin = _campo(hasta[0], 0.7, hasta[1], nota=f"la fecha fin no coincide con el plazo de {meses.group(1)} meses")
            elif meses:
                fin.nota = f"rango explícito, consistente con el plazo de {meses.group(1)} meses"
            return inicio, fin
    if meses:
        inicio = _fecha_firma(texto, fecha_correo) if re.search(r"firma", cuerpo, re.I) else _vacio("el plazo no indica desde cuándo corre")
        if inicio.valor:
            n = int(meses.group(1))
            nota = f"derivada: inicio + {n} meses − 1 día (convención de los contratos del buzón). Requiere confirmación"
            return inicio, _campo(fin_por_plazo(inicio.valor, n), 0.6, meses.group(0), "derivado", nota)
        return inicio, _vacio("hay plazo en meses pero no fecha de inicio")
    fecha = primera_fecha(cuerpo)
    return _vacio("no se encontró la fecha de inicio"), _campo(fecha[0], 0.9, fecha[1]) if fecha else _vacio("no se encontró la fecha fin")


def _normalizar_tipo_poliza(tipo: str) -> list[str]:
    partes = re.split(r",|\s+y\s+", sin_tildes(tipo.lower()))
    return [re.sub(r"\s+", "_", p.strip()) for p in partes if p.strip()]


def _extraer_poliza(texto: str) -> tuple[CampoExtraido, CampoExtraido, CampoExtraido, list[str]]:
    oraciones = [o for o in re.split(r"(?<=\.)\s+", texto) if re.search(r"p[oó]liza", o, re.I)]
    if not oraciones:
        nota = "no hay cláusula de garantías/póliza"
        return _campo(False, 0.85, None, nota=nota), _campo("", 0.85, None, nota=nota), _campo("no_aplica", 0.85, None, "derivado", nota), []
    tipos: list[str] = []
    for o in oraciones:
        for m in _POLIZA_TIPO.finditer(o):
            tipos += [t for t in _normalizar_tipo_poliza(m.group(1)) if t not in tipos]
    evidencia = colapsar_espacios(oraciones[0])[:240]
    advertencias = []
    confianza = 0.95
    if any(_CONDICIONAL.search(o) for o in oraciones):
        confianza = 0.8
        advertencias.append(f"póliza condicional: {evidencia}")
    tipo = _campo(";".join(tipos), confianza if tipos else 0.5, evidencia, nota=None if tipos else "no se identificó el tipo de póliza")
    estado = _campo("pendiente", 0.95, None, "derivado", "registro nuevo con póliza → pendiente")
    return _campo(True, confianza, evidencia), tipo, estado, advertencias


def _objeto(cuerpo: str | None) -> CampoExtraido:
    if not cuerpo:
        return _vacio("no se encontró la cláusula de objeto")
    limpio = _BOILERPLATE_OBJETO.sub("", colapsar_espacios(cuerpo)).rstrip(".")
    limpio = limpio[0].upper() + limpio[1:]
    if len(limpio) > 200:
        limpio = limpio[:197].rsplit(" ", 1)[0] + "..."
    return _campo(limpio, 0.9, colapsar_espacios(cuerpo)[:240])


def _extraer_otrosi(texto: str, base: dict) -> dict:
    """Un otrosí solo trae los campos que modifica; el resto queda en null (no cambia)."""
    clausulas = _clausulas(texto)
    plazo = next((c for _, c in clausulas if "(PLAZO)" in c.upper()), None)
    valor_txt = next((c for _, c in clausulas if "(VALOR)" in c.upper()), None)
    fecha = primera_fecha(plazo) if plazo else None
    base["fecha_fin"] = _campo(fecha[0], 0.9, fecha[1], nota="nueva fecha fin según otrosí") if fecha else _vacio()
    if valor_txt:
        base["valor"], base["moneda"], _ = _extraer_valor(valor_txt)
    if _AMPLIAR_GARANTIAS.search(texto):
        nota = "el otrosí exige ampliar la vigencia de las garantías: la póliza vuelve a 'pendiente'"
        base["estado_poliza"] = _campo("pendiente", 0.85, _AMPLIAR_GARANTIAS.search(texto).group(0)[:200], "derivado", nota)
    return base


def sin_contrato(mensaje_id: str, motivo: str) -> ContratoExtraido:
    """Resultado de extracción para mensajes sin adjunto de contrato (se clasifican como rechazados)."""
    vacios = {nombre: _vacio() for nombre in ContratoExtraido.model_fields if nombre not in
              ("mensaje_id", "tipo_documento", "adjunto", "valor_indeterminado", "advertencias")}
    return ContratoExtraido(mensaje_id=mensaje_id, tipo_documento="otro", adjunto="", advertencias=[motivo], **vacios)


def extraer_contrato(mensaje_id: str, adjunto: str, texto: str, fecha_correo: date | None) -> ContratoExtraido:
    if not texto.strip():
        raise ExtraccionError(f"el adjunto '{adjunto}' del mensaje {mensaje_id} está vacío")
    tipo = detectar_tipo(texto)
    campos: dict = {nombre: _vacio() for nombre in (
        "id_contrato", "cliente", "nit_cliente", "pais", "objeto", "valor", "moneda",
        "fecha_inicio", "fecha_fin", "requiere_poliza", "tipo_poliza", "estado_poliza")}
    resultado = {"mensaje_id": mensaje_id, "tipo_documento": tipo, "adjunto": adjunto, "advertencias": []}
    if tipo == "otro":
        resultado["advertencias"].append(f"el adjunto '{adjunto}' no es un contrato: es {describir_documento(texto)}")
        return ContratoExtraido(**resultado, **campos)

    id_match = _ID.search(texto)
    campos["id_contrato"] = (
        _campo(id_match.group(1), 0.95, colapsar_espacios(id_match.group(0)))
        if id_match else _vacio("el documento no trae número; se asignará AUTO-<año>-<secuencia> al registrar")
    )
    campos["cliente"], campos["nit_cliente"], campos["pais"] = _extraer_parte(texto)

    if tipo == "otrosi":
        return ContratoExtraido(**resultado, **_extraer_otrosi(texto, campos))

    clausulas = _clausulas(texto)
    cuerpo_valor = _clausula(clausulas, "VALOR")
    campos["objeto"] = _objeto(_clausula(clausulas, "OBJETO"))
    campos["valor"], campos["moneda"], indeterminado = _extraer_valor(cuerpo_valor)
    if campos["moneda"].valor is None:
        campos["moneda"] = _moneda_por_palabra(cuerpo_valor)
    if campos["moneda"].valor is None and campos["pais"].valor:
        pais = campos["pais"].valor
        campos["moneda"] = _campo(MONEDA_POR_PAIS[pais], 0.8, None, "derivado", f"sin valor en el contrato; moneda por defecto de {pais}")
    campos["fecha_inicio"], campos["fecha_fin"] = _extraer_fechas(_clausula(clausulas, "PLAZO", "DURACION"), texto, fecha_correo)
    campos["requiere_poliza"], campos["tipo_poliza"], campos["estado_poliza"], avisos = _extraer_poliza(texto)
    resultado["advertencias"] += avisos
    resultado["valor_indeterminado"] = indeterminado
    return ContratoExtraido(**resultado, **campos)
