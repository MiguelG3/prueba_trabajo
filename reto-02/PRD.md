# Reto técnico 02 — Agente conversacional "Registro de Contratos Vigentes"

> Proceso de selección · Equipo Perxia 2.0 · Periferia IT Group
> Versión 2.0 · 2026-09-03 · Documento entregado al candidato al inicio de la sesión

---

## 0. Ficha del reto

| Elemento | Definición |
|---|---|
| **Duración** | Se comunica al inicio de la sesión. |
| **Reto** | Construir un **agente conversacional completo e independiente** que automatice el registro de contratos vigentes desde un buzón único: interfaz de chat, backend con el ciclo del agente y sus herramientas, y conexión a un modelo de lenguaje. Debe poder probarse desde un link. |
| **Lenguaje** | TypeScript (Bun o Node 20+) en backend y herramientas. Front libre: React, Svelte, Vue o HTML plano. |
| **Modelo de lenguaje** | El que elijas (Anthropic, OpenAI, Google, Azure, Mistral, local). La clave es tuya. Se lee de variable de entorno y **nunca** aparece en el repositorio, el front ni los logs. |
| **IA permitida** | Cualquier asistente de IA para construir (Claude, ChatGPT, Gemini, Copilot, Cursor, etc.). Debes **declarar cuáles usaste y para qué** en `SOLUCION.md`. Debes poder explicar cada línea que entregas. |
| **Lo que NO recibes** | Código ni acceso a ningún producto de Periferia. El reto es independiente: se resuelve con los fixtures adjuntos y este documento. |
| **Entregable** | Repositorio con la aplicación, link para probarla, `demo.ts` que ejecuta las herramientas sin modelo, y `SOLUCION.md` con el planteamiento de la solución. Ver sección 9. |
| **Bonus** | Hasta +10 puntos si además entregas el agente empaquetado como módulo reutilizable (sección 9.4). |
| **Aprobación** | 70 / 100 puntos según la rúbrica de la sección 10. |

---

## 1. Resumen ejecutivo

El registro maestro de contratos vigentes de Periferia está **congelado desde el 30 de mayo de 2026**. Lo alimentaba un aprendiz que ya no está. Hoy solo llegan a administración los contratos que requieren póliza, porque el área comercial los envía únicamente en ese caso. No hay área legal interna ni un punto único de recepción.

El reto consiste en construir un agente que actúe como **punto único de recepción**: lee los correos de un buzón, extrae los datos del contrato adjunto, detecta duplicados y actualizaciones, archiva el documento en una estructura de carpetas tipo SharePoint y alimenta el maestro. Para campos de baja confianza pide revisión humana antes de registrar. Además produce alertas de vencimiento y de pólizas pendientes.

Este problema es tanto de proceso como de automatización. Por eso el entregable incluye una **propuesta de regla de gobierno** de una página: qué deben enviar los comerciales, a dónde y qué pasa si no lo hacen.

---

## 2. Contexto y problema

### 2.1 Situación actual (as-is)

| Dimensión | Hoy |
|---|---|
| Fuente de verdad | Un Excel maestro en SharePoint, congelado al 2026-05-30. |
| Flujo de entrada | Los comerciales envían el contrato a administración **solo si requiere póliza**. El resto no llega. |
| Recepción | No hay buzón ni responsable único. Llegan a personas distintas por correo. |
| Área legal | No existe internamente. Nadie revisa cláusulas. |
| Archivo | Los PDF quedan dispersos en correos personales. |
| Consecuencia | No se sabe qué contratos están vigentes, cuáles vencen ni qué pólizas están por expirar. |

### 2.2 Dolor que resolvemos

1. **Visibilidad**: la dirección no puede responder "¿qué contratos vencen este trimestre?".
2. **Riesgo**: pólizas exigidas contractualmente que no se constituyen o vencen sin renovación.
3. **Dependencia**: el proceso murió cuando se fue una persona. Debe sobrevivir a rotación.

### 2.3 Lo que este reto NO resuelve

- Conexión real a Exchange ni a SharePoint (se simulan con carpetas locales).
- Revisión jurídica del contenido del contrato.
- Reconstrucción histórica de los contratos de junio a agosto (se diseña en la regla de gobierno, no se ejecuta).

---

## 3. Objetivos y no-objetivos

### 3.1 Objetivos

| # | Objetivo | Métrica de éxito en el reto |
|---|---|---|
| O1 | Registrar todo contrato que llegue al buzón, con o sin póliza. | Los 6 mensajes de `fixtures/reto-02/buzon/` quedan clasificados y los registrables terminan en el maestro. |
| O2 | No corromper el maestro. | Cero filas duplicadas. Una actualización (otrosí) modifica la fila existente y conserva historial en `historial.jsonl`. |
| O3 | Convertir baja confianza en revisión humana, no en datos malos. | Campos con confianza < 0.8 detienen el registro y el agente pide confirmación. |
| O4 | Anticipar riesgos. | Se genera un reporte con contratos que vencen en ≤ 60 días y pólizas pendientes. |

### 3.2 No-objetivos

- OCR de PDF escaneados. Los fixtures traen el texto del contrato ya extraído en `contrato.txt`. Leer PDF nativo con texto es P1 opcional.
- Autenticación de usuarios, roles o multiusuario.
- Base de datos. El maestro es un CSV; SharePoint es una carpeta local.

---

## 4. Usuarios y actores

| Actor | Rol en el flujo | Interacción con el agente |
|---|---|---|
| **Comercial** | Cierra el contrato con el cliente. | Envía el PDF al buzón único. No usa el agente. |
| **Analista administrativa** (usuaria principal) | Dueña del maestro y del seguimiento de pólizas. | Conversa con el agente en el chat: "procesa el buzón". Confirma campos dudosos. Consume alertas. |
| **Gerencia** | Necesita visibilidad de vigencias y riesgo. | Lee el reporte de alertas. |
| **Aseguradora / corredor** | Constituye pólizas. | Fuera del sistema. |

---

## 5. Historias de usuario y criterios de aceptación

### HU-1 · Leer el buzón
**Como** analista, **quiero** ver qué mensajes hay pendientes y qué trae cada uno, **para** decidir por dónde empiezo.

Criterios de aceptación:
- `contratos_leer_buzon` lista todos los mensajes de `fixtures/reto-02/buzon/` no procesados (no presentes en `out/procesados.json`) con `{ id, de, asunto, fecha, adjuntos[], tiene_contrato: boolean }`.
- Un mensaje sin adjunto de contrato (por ejemplo una cotización o una pregunta) se marca `tiene_contrato: false` y se clasifica como `rechazado` con motivo.

### HU-2 · Extraer los datos del contrato
**Como** analista, **quiero** que el agente lea el contrato y me entregue sus datos estructurados con nivel de confianza, **para** no leer 20 páginas por contrato.

Criterios de aceptación:
- `contratos_extraer` devuelve el esquema de la sección 7.2 con `confianza` por campo en `[0, 1]`.
- La extracción determinista (regex, heurísticas sobre `contrato.txt`) es P0. Enriquecerla con el modelo desde el prompt del agente es válido, pero **el valor final que se registra debe pasar por `contratos_validar`**.
- Campos ausentes en el texto son `null` con confianza `0`, nunca inventados.

### HU-3 · Validar y clasificar
**Como** analista, **quiero** saber si el contrato es nuevo, actualiza uno existente o es duplicado, **para** no ensuciar el maestro.

Criterios de aceptación:
- `contratos_validar` clasifica en `nuevo`, `actualizacion`, `duplicado` o `rechazado` según las reglas RN1–RN4.
- Devuelve `requiere_revision: string[]` con los campos de confianza < 0.8 y los conflictos con el maestro.
- El comercial remitente se resuelve contra `comerciales.json`; un remitente desconocido se reporta pero no bloquea.

### HU-4 · Registrar y archivar
**Como** analista, **quiero** que el contrato quede en el maestro y el PDF archivado en la carpeta correcta, **para** que cualquiera lo encuentre.

Criterios de aceptación:
- `contratos_registrar` solo escribe si `requiere_revision` está vacío **o** el argumento `confirmado = true`.
- Escribe o actualiza la fila en `out/sharepoint/maestro-contratos.csv` (copia del fixture, nunca el fixture).
- Copia el contrato a `out/sharepoint/Contratos/<año_inicio>/<cliente-slug>/<id_contrato>.<ext>` y guarda la ruta en la fila.
- Agrega una línea en `out/sharepoint/historial.jsonl` con `{ ts, id_contrato, accion, cambios, mensaje_id }`.
- Marca el mensaje en `out/procesados.json`.

### HU-5 · Alertar
**Como** gerencia, **quiero** un reporte de riesgos, **para** actuar antes de que un contrato o póliza venza.

Criterios de aceptación:
- `contratos_alertas` produce `out/alertas.md` con tres secciones: vencen en ≤ 60 días, `requiere_poliza = true` con `estado_poliza != vigente`, y contratos registrados desde el 2026-05-30 (gap cubierto).
- La fecha de referencia se pasa como argumento (`hoy`) para que la demo sea determinista.

### HU-6 · Manejo de errores
- Texto vacío, fecha inválida, moneda desconocida: la herramienta devuelve `{ ok: false, error }` legible y el agente sigue con el siguiente mensaje.
- El agente nunca aborta el lote completo por un mensaje malo.

---

## 6. Arquitectura requerida

Construyes un agente conversacional de extremo a extremo. Tienes libertad de framework, pero no de forma: estos componentes y este contrato de herramientas son obligatorios, porque son lo que evaluamos.

### 6.1 Componentes

```
┌──────────────┐  HTTP / WS   ┌─────────────────────────────────────────┐
│  Front: chat │ ───────────▶ │  Backend                                │
│  - historial │ ◀─────────── │  - ciclo del agente (prompt → modelo →  │
│  - tool calls│              │    llamadas a herramientas → respuesta) │
│  - confirmar │              │  - herramientas tipadas (zod)           │
└──────────────┘              │  - adaptador de proveedor LLM           │
                              │  - sesiones en memoria o archivo        │
                              └───────┬─────────────────────┬───────────┘
                                      │                     │
                               fixtures/ (solo lectura)   out/ (escritura)
```

| Componente | Obligatorio | Detalle |
|---|---|---|
| **Front de chat** | Sí | Historial de la conversación, campo de entrada, indicador de "pensando". Debe **mostrar cada llamada a herramienta** (nombre, argumentos, resultado resumido) y **resaltar cuando el agente pide confirmación**. Streaming opcional. |
| **Backend** | Sí | Expone la API del chat. Ejecuta el ciclo del agente con tope de iteraciones. Mantiene la sesión (memoria o archivo). |
| **Herramientas** | Sí | Funciones tipadas con `zod`, separadas del servidor HTTP e importables desde `demo.ts`. Son la **única** fuente de valores que el agente puede afirmar. |
| **Adaptador LLM** | Sí | Una interfaz propia (`enviar(mensajes, herramientas) → respuesta`) con una implementación para el proveedor que elijas. Cambiar de proveedor no debe tocar el ciclo del agente. |
| **System prompt** | Sí | En un archivo Markdown aparte (`agent/prompt.md`), no embebido en código. |
| **Persistencia** | No | Memoria o archivos en `out/` bastan. Sin base de datos. |
| **Autenticación** | No | El link puede ser público. Si lo proteges, entrega la clave de acceso en el README. |

### 6.2 Contrato de herramientas

Cada herramienta es un objeto con tres miembros. El nombre que ve el modelo es `<archivo>_<export>`.

```ts
// src/tools/contratos.ts
import { z } from "zod"

export const leer_buzon = {
  description: "…una frase: es lo único que el modelo lee para decidir cuándo llamarla",
  args: {},
  async execute(_args: {}, ctx: { directory: string; sessionId: string }) {
    // ctx.directory = raíz del proyecto; resuelve rutas desde aquí, nunca absolutas
    return JSON.stringify({ ok: true, data: { /* ... */ } })
  },
}
```

| Miembro | Regla |
|---|---|
| `description` | Una frase precisa. |
| `args` | Objeto de esquemas `zod` con `.describe()` en cada campo. El backend valida antes de ejecutar y devuelve el error al modelo si no cumple. |
| `execute(args, ctx)` | Devuelve **string** (JSON serializado) con `{ ok: true, data }` o `{ ok: false, error }`. **Nunca lanza.** |

Contrato mínimo de este reto:

| Herramienta | Entrada | Salida (`data`) | Prioridad |
|---|---|---|---|
| `contratos_leer_buzon` | `{}` | `{ mensajes[] }` | P0 |
| `contratos_extraer` | `{ mensaje_id }` | `Contrato` con `confianza` por campo | P0 |
| `contratos_validar` | `{ mensaje_id, contrato }` | `{ clasificacion, id_contrato_existente?, requiere_revision[], diferencias? }` | P0 |
| `contratos_registrar` | `{ mensaje_id, contrato, confirmado?: boolean }` | `{ id_contrato, accion, ruta_archivo }` o `{ ok: false, error: "requiere revisión: ..." }` | P0 |
| `contratos_alertas` | `{ hoy: "YYYY-MM-DD" }` | `{ ruta, vencen[], polizas_pendientes[], registrados_desde_corte[] }` | P0 |
| `contratos_leer_pdf` | `{ ruta }` | `{ texto }` | P1 opcional |

### 6.3 Reglas del ciclo del agente

| # | Regla |
|---|---|
| CA1 | Tope de iteraciones herramienta → modelo por turno (sugerido 25). Al alcanzarlo, el agente responde con lo que tiene y lo que falta. |
| CA2 | El modelo **no puede afirmar un valor** que no haya salido de una herramienta. El prompt lo prohíbe; el diseño lo hace innecesario. |
| CA3 | **Confirmación humana**: cuando una acción la requiere, el agente termina el turno con una pregunta explícita. Solo procede si el siguiente mensaje del usuario confirma. El front resalta ese estado. |
| CA4 | Toda llamada a herramienta queda en el historial visible del chat y en `out/log.jsonl`. |
| CA5 | Un error de herramienta o del proveedor LLM se muestra en el chat en lenguaje claro. La sesión no muere. |

### 6.4 API mínima

Diseño libre, pero documentado en el README. Referencia:

| Método | Ruta | Cuerpo / respuesta |
|---|---|---|
| `POST` | `/api/chat` | `{ sessionId, message }` → `{ reply, toolCalls[], needsConfirmation }` (o stream de eventos) |
| `GET` | `/api/sessions/:id` | Historial completo de la sesión |
| `GET` | `/api/health` | `{ ok: true, provider, model }` sin exponer claves |

### 6.5 Estructura sugerida del repositorio

```
reto-02/
├── agent/
│   └── prompt.md                        # system prompt del agente
├── src/
│   ├── server.ts                        # API HTTP y ciclo del agente
│   ├── llm/
│   │   ├── adapter.ts                   # interfaz del proveedor
│   │   └── <proveedor>.ts               # implementación elegida
│   ├── tools/
│   │   └── contratos.ts                 # herramientas (cada export → contratos_<export>)
│   └── knowledge/
│       └── registro-contratos.md  # conocimiento del proceso que el agente consulta
├── web/                                 # front de chat
├── fixtures/                            # entregados por Periferia (no modificar)
├── out/                                 # generado en ejecución
├── demo.ts                              # herramientas sin modelo
├── .env.example                         # variables requeridas, sin valores
├── package.json
├── README.md
└── SOLUCION.md
```

Separación que evaluamos: **comportamiento** en `agent/prompt.md`, **conocimiento** en `src/knowledge/`, **ejecución** en `src/tools/`. Un cambio de reglas de negocio no debería tocar el servidor.

### 6.6 `demo.ts` (verificación sin modelo)

Procesa los 6 mensajes del buzón en orden llamando directamente a las herramientas. Imprime por mensaje: clasificación, campos en revisión, acción tomada. Los mensajes con `requiere_revision` no vacío deben quedar **sin registrar** en la primera pasada, y la demo debe mostrar una segunda llamada con `confirmado: true` para uno de ellos. Debe correr sin clave de ningún proveedor:

```bash
bun install && bun run demo.ts
```

---

## 7. Requisitos funcionales detallados

### 7.1 Fixtures

| Ruta | Contenido |
|---|---|
| `fixtures/reto-02/buzon/<mensaje_id>/correo.json` | `{ id, de, para, asunto, fecha, cuerpo, adjuntos[] }`. |
| `fixtures/reto-02/buzon/<mensaje_id>/<adjunto>` | Texto del contrato (`contrato.txt`) u otro adjunto. |
| `fixtures/reto-02/maestro-contratos.csv` | El maestro congelado al 2026-05-30. Encabezados en la sección 7.2. |
| `fixtures/reto-02/comerciales.json` | `{ email, nombre, region }[]`. |

### 7.2 Esquema del maestro (una fila por contrato)

| Columna | Tipo | Regla |
|---|---|---|
| `id_contrato` | string | Número del contrato tal como aparece en el documento. Si no existe, `AUTO-<año>-<secuencia>`. |
| `cliente` | string | Razón social de la contraparte. |
| `nit_cliente` | string | Identificador tributario sin dígito de verificación ni puntos. |
| `pais` | `CO\|EC\|PE\|PA\|HN` | Inferido del identificador o del texto. |
| `objeto` | string | Máx. 200 caracteres. |
| `valor` | number | Sin separadores. `0` si el contrato es por demanda (`valor_indeterminado = true`). |
| `moneda` | `COP\|USD\|PEN\|PAB\|HNL` | |
| `fecha_inicio` / `fecha_fin` | `YYYY-MM-DD` | `fecha_fin` puede derivarse de plazo en meses. |
| `requiere_poliza` | boolean | |
| `tipo_poliza` | string | Lista separada por `;`. Vacío si no requiere. |
| `estado_poliza` | `vigente\|pendiente\|vencida\|no_aplica` | Nuevo registro con póliza → `pendiente`. |
| `comercial` | string | Nombre resuelto desde `comerciales.json`. |
| `ruta_sharepoint` | string | Ruta relativa en `out/sharepoint/`. |
| `fecha_registro` | `YYYY-MM-DD` | |
| `fuente` | `buzon\|manual\|migracion` | |

### 7.3 Reglas de negocio

| # | Regla |
|---|---|
| RN1 | **Duplicado**: mismo `id_contrato` y mismos `valor`, `fecha_inicio`, `fecha_fin`. No se escribe nada; se reporta. |
| RN2 | **Actualización**: mismo `id_contrato` (o mismo `nit_cliente` + `objeto` con similitud ≥ 0.9) y algún campo distinto, o el documento se identifica como otrosí. Se actualiza la fila y se registra el cambio en `historial.jsonl`. |
| RN3 | **Nuevo**: no hay coincidencia. Se inserta. |
| RN4 | **Rechazado**: sin adjunto de contrato, o texto que no contiene partes ni objeto identificables. Se reporta con motivo. |
| RN5 | Campos con confianza < 0.8 → `requiere_revision`. `contratos_registrar` sin `confirmado = true` los rechaza. |
| RN6 | El fixture `maestro-contratos.csv` es de solo lectura. La primera ejecución lo copia a `out/sharepoint/`. |
| RN7 | Toda ejecución deja `out/log.jsonl` con `{ ts, herramienta, mensaje_id, ok, resumen }`. |

### 7.4 Casos incluidos en el buzón

| Mensaje | Qué prueba | Resultado esperado |
|---|---|---|
| `msg-001` | Contrato nuevo con póliza de cumplimiento, todo legible. | `nuevo`, registrado, `estado_poliza = pendiente`. |
| `msg-002` | Contrato nuevo **sin** póliza (hoy nunca llegaría). | `nuevo`, registrado, `no_aplica`. |
| `msg-003` | Otrosí que extiende `fecha_fin` de un contrato ya en el maestro. | `actualizacion`, fila modificada, historial. |
| `msg-004` | Reenvío de un contrato ya registrado en el maestro. | `duplicado`, sin escritura. |
| `msg-005` | Correo con una cotización, sin contrato. | `rechazado`. |
| `msg-006` | Contrato con valor por demanda y plazo expresado en meses, remitente no registrado. | `nuevo` con `requiere_revision` (valor, fecha_fin). No se registra hasta confirmar. |

### 7.5 Propuesta de regla de gobierno (documentación obligatoria)

Una página en `SOLUCION.md` que responda:
1. **Canal único**: dirección del buzón y quién lo administra.
2. **Obligación del comercial**: qué debe enviar (PDF firmado, otrosíes, actas de terminación), en qué plazo desde la firma, y con qué formato de asunto.
3. **Acuse automático**: qué responde el agente al comercial y en cuánto tiempo.
4. **Excepciones y escalamiento**: qué pasa con un contrato que llega sin firmar o sin valor, y a quién se escala.
5. **Cierre del gap**: cómo se reconstruye junio–agosto de 2026 en una sola campaña.
6. **Indicador**: una métrica mensual que demuestre que el proceso vive (por ejemplo, % de contratos facturados que existen en el maestro).

---

## 8. Requisitos no funcionales

| Categoría | Requisito |
|---|---|
| Arranque | Un comando levanta front y backend en local (`bun run dev` o `docker compose up`). Menos de 2 minutos en máquina limpia con las variables de `.env.example`. |
| Determinismo | `demo.ts` produce el mismo resultado en ejecuciones consecutivas (salvo timestamps). `out/` se limpia al inicio. |
| Seguridad | Clave del modelo solo en variable de entorno del backend. Nunca en el front, el repositorio, los logs ni las respuestas de la API. Sin credenciales ni datos personales reales. Las herramientas no ejecutan comandos de shell. |
| Costo | Tope de iteraciones por turno y tope de tokens por sesión configurables. Un usuario no puede gastar tu clave sin límite. |
| Robustez | Errores tipados `{ ok: false, error }`. Timeout al proveedor LLM con mensaje claro. Un caso malo no impide el siguiente. |
| Legibilidad | TypeScript sin `any`, funciones cortas, nombres consistentes. |
| Dependencias | Las que necesites, justificadas en `SOLUCION.md`. `zod` obligatorio para los argumentos de herramientas. |

---

## 9. Entregable y documentación

### 9.1 `SOLUCION.md` — estructura obligatoria

1. **Problema en una frase** y a quién le duele.
2. **Arquitectura**: diagrama front → backend → herramientas → archivos, y dónde vive el prompt, el conocimiento y la ejecución.
3. **Ciclo del agente**: cómo implementaste el bucle, el tope de iteraciones y la confirmación humana.
4. **Elección del modelo**: proveedor, modelo, por qué, y costo estimado por caso procesado.
5. **Estrategia de extracción**: cómo encuentras partes, valor, plazo y póliza; cómo calculas la confianza; dónde entra el modelo y dónde no.
6. **Regla de gobierno** (sección 7.5).
7. **Decisiones y trade-offs**: mínimo 3, con la alternativa descartada y por qué.
8. **Supuestos** que tomaste al interpretar este PRD.
9. **Cobertura**: tabla de historias de usuario con estado (hecho / parcial / no hecho) y qué falta para producción.
10. **Uso de IA**: qué asistentes usaste para construir, para qué tareas, qué descartaste de lo que te propusieron y por qué.
11. **Riesgos** de llevar esto a producción y cómo los mitigarías.

### 9.2 `README.md`

Cómo levantar en local (un comando), variables de entorno requeridas, cómo correr `demo.ts`, el link de prueba y, si aplica, la clave de acceso al link.

### 9.3 Link para probar

URL pública donde el agente responde (Vercel, Render, Fly.io, Railway, Azure, un túnel estable, o el proveedor que prefieras). Debe estar activo durante la defensa. Si el despliegue no fue posible, se acepta correrlo en local durante la defensa con penalización de `-10`.

### 9.4 Bonus: módulo reutilizable (hasta +10)

Entrega además una carpeta `modulo/` con el agente empaquetado para integrarse a otras plataformas de agentes, sin depender de tu servidor:

```
modulo/
├── agent.md            # frontmatter: description, mode: primary, permission {edit: deny, bash: deny}; cuerpo: el system prompt
├── tools/contratos.ts     # las mismas herramientas, importables sin el servidor
└── skill/registro-contratos/SKILL.md   # frontmatter: name, description; cuerpo: el conocimiento del proceso
```

Se evalúa que las tres piezas sean las mismas que usa tu aplicación (no copias divergentes).

### 9.5 Forma de entrega

Repositorio Git con historial de commits, o `reto-02-<apellido>.zip`. Sin `node_modules/`, sin `out/`, sin `.env`.

---

## 10. Riesgos, supuestos y preguntas abiertas

| Tipo | Contenido |
|---|---|
| Supuesto | Los contratos llegan como PDF con texto (no escaneados). En producción habrá escaneos y se necesitará OCR. |
| Supuesto | El comercial cumple la regla de gobierno. Sin ella, el agente registra solo lo que le llega, igual que hoy. |
| Riesgo | Falsos duplicados por variaciones en el nombre del cliente. Mitigación: dedupe por `nit_cliente` antes que por nombre. |
| Riesgo | El modelo "redondea" el valor o infiere una fecha. Mitigación: el valor registrado sale de la herramienta, no del modelo; el modelo solo puede proponer y el humano confirma. |
| Pregunta abierta | ¿Quién es el dueño del maestro cuando no hay área legal? Debe quedar en la regla de gobierno. |

---

## 11. Prompt de ejemplo para la demo

En tu chat, sesión nueva:

```
Procesa el buzón de contratos con fecha de hoy 2026-09-03. Registra lo que
esté limpio, muéstrame lo que requiere revisión campo por campo y termina
con el reporte de alertas. No registres nada dudoso sin preguntarme.
```

Resultado esperado: Tabla por mensaje con clasificación y acción, llamadas a herramientas visibles, detalle de `msg-006` con pregunta de confirmación, y resumen de `out/alertas.md`. Tras "confirmo el valor 0 y la fecha fin 2027-08-31", `msg-006` queda registrado.
