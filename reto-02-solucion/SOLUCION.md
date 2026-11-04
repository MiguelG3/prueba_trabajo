# SOLUCIÓN — Reto 02 · Agente "Registro de Contratos Vigentes"

## 0. Nota sobre el stack

El PRD pide TypeScript + zod. Por requerimiento explícito se implementó en **Python + LangGraph**, conservando
todo el contrato evaluable:

| PRD (TypeScript) | Esta solución (Python) |
|---|---|
| `zod` con `.describe()` | `pydantic` con `Literal` (→ `enum` en el JSON schema) y `Field(description=…)` |
| `src/tools/contratos.ts` → `contratos_<export>` | `src/tools/contratos.py` → mismos nombres `contratos_*` |
| `execute(args, ctx) → string` que nunca lanza | `Tool.execute(args, ctx) → str` JSON `{ok, data}` / `{ok:false, error}` |
| `demo.ts` (`bun run demo.ts`) | `demo.py` (`uv run demo.py`) |
| `bun run dev` | `uv run dev` |

## 1. Problema en una frase

El maestro de contratos está congelado desde el 2026-05-30 y solo recibía los contratos con póliza. La
**analista administrativa** no sabe qué está vigente, y **gerencia** no ve vencimientos ni pólizas en riesgo.
El proceso murió cuando se fue la única persona que lo llevaba.

## 2. Arquitectura

```
┌───────────────────────┐  POST /api/chat   ┌──────────────────────────────────────────────────────────┐
│ web/ (HTML + JS)      │ ────────────────▶ │ src/server.py  (FastAPI)                                 │
│ - historial           │ ◀──────────────── │   └─ src/graph.py  Agente (LangGraph StateGraph)         │
│ - tarjetas tool calls │  reply, toolCalls,│        agente ⇄ herramientas, MemorySaver por sessionId  │
│ - banner confirmación │  needsConfirmation│        ├─ src/llm/adapter.py  LLMAdapter.enviar()        │
└───────────────────────┘                   │        │    presets: gemini·groq·openrouter·ollama (gratis)│
                                            │        │             openai·anthropic·openai_compatible   │
                                            │        └─ src/tools/contratos.py  (Tool + pydantic)       │
                                            │              └─ src/dominio/  extracción · clasificación  │
                                            │                               números/fechas · maestro CSV │
                                            └───────────────┬───────────────────────────┬──────────────┘
                                                            │ solo lectura              │ escritura atómica
                                                    fixtures/reto-02/            out/  sharepoint/maestro-contratos.csv
                                                    (buzón, maestro, comerciales)       sharepoint/historial.jsonl
                                                                                        sharepoint/Contratos/<año>/<cliente>/
                                                                                        procesados.json · alertas.md · log.jsonl
```

| Capa | Dónde vive | Qué cambia si cambia el negocio |
|---|---|---|
| **Comportamiento** | `agent/prompt.md` | Cómo conversa, qué formato entrega, cuándo pregunta |
| **Conocimiento** | `src/knowledge/registro-contratos.md` | Reglas del proceso que el agente explica |
| **Contrato de herramientas** | `src/tools/schemas.py` | Tipos, enums y descripciones que ve el modelo |
| **Ejecución** | `src/tools/` + `src/dominio/` | Reglas deterministas (RN1–RN7, umbrales, archivo) |

El servidor no contiene reglas de negocio. `cargar_system_prompt()` concatena el prompt y el conocimiento, y
`modulo/` se genera desde esos mismos archivos.

## 3. Ciclo del agente

Es un `StateGraph` propio. No uso `create_react_agent` porque necesito controlar el tope y el guard:

```
START → agente ──(tool_calls)──▶ herramientas ──▶ agente … ──(sin tool_calls)──▶ END
```

- **Estado** (checkpointer `MemorySaver`, `thread_id = sessionId`): `messages`, `turno` (reducer suma: +1 por
  mensaje del usuario), `iteraciones` (se reinicia en cada turno), `tokens_sesion` y `pendientes`
  (`mensaje_id → campos en revisión + turno en que se presentaron`).
- **Tope de iteraciones (CA1).** Al llegar a `MAX_ITERACIONES` (25), el nodo `agente` se invoca **sin
  herramientas** y con un aviso para que resuma lo hecho y lo que falta. Si aun así el modelo devuelve
  tool_calls, se descartan para no dejar llamadas colgando en el historial. Hay además un tope de tokens por
  sesión y uno global por proceso, que protegen la llave de un link público.
- **Confirmación humana (CA3), aplicada en código y no solo en el prompt:**
  1. Cuando `contratos_validar` o `contratos_registrar` devuelven `requiere_revision`, el grafo guarda los
     campos en `pendientes` con el número de turno, y la API responde `needsConfirmation: true` (el front
     muestra un banner amarillo con campo, valor, confianza y motivo).
  2. `contratos_registrar(confirmado=true)` pasa por un **guard** antes de ejecutarse. Se rechaza si no hay
     pendientes para ese mensaje, si se presentaron **en el mismo turno** (el modelo intentó autoconfirmarse)
     o si corrige campos que el usuario nunca vio.
  3. Además, la herramienta misma rechaza registrar con `requiere_revision` sin `confirmado` (RN5). Son dos
     capas independientes.
- **Errores (CA5).** Timeout, 429 del free tier, llave inválida o modelo inexistente se traducen a un mensaje
  claro en el chat y la sesión sigue viva. Las herramientas nunca lanzan: devuelven `{ok:false, error}`.
- **Trazabilidad (CA4).** Cada llamada queda en el historial visible (tarjeta desplegable con argumentos y
  resultado) y en `out/log.jsonl` `{ts, herramienta, mensaje_id, ok, resumen}`. Los rechazos del guard también
  quedan en el log.

## 4. Elección del modelo y costo

**Decisión: el modelo es configuración, no código.** El adaptador propio (`LLMAdapter.enviar(mensajes,
herramientas)`) tiene tres implementaciones sobre LangChain: OpenAI-compatible, Gemini nativo y Claude nativo.
Cambiar de proveedor es cambiar `LLM_PROVIDER` / `LLM_MODEL`.

- **Default: Gemini (`gemini-3.5-flash-lite`) en el free tier de AI Studio.** Se fija una versión concreta y no el
  alias `gemini-flash-latest`: en las pruebas reales el alias devolvió `503 UNAVAILABLE` (alta demanda) y además
  cambia de modelo sin aviso. Llave gratis y sin tarjeta, buen
  tool calling y buen español. Uso la integración **nativa** (`langchain-google-genai`) y no la capa
  OpenAI-compatible, porque los Gemini recientes exigen devolver *thought signatures* en el tool calling
  multi-turno.
- **Producción recomendada: un modelo de pago con retención de datos controlada.** Los contratos son
  información confidencial y los free tiers pueden usar los datos para mejorar el modelo.
  - Claude Sonnet 5.5 (`claude-sonnet-5-5`) es el punto medio.
  - Claude Opus 5.5 (`claude-opus-5-5`, default del preset `anthropic`) es para la máxima calidad.
  - Para Claude el adaptador no envía `temperature` (los modelos actuales la rechazan), expone `LLM_EFFORT` y
    activa el fallback server-side ante rechazos de seguridad.

**Costo estimado.** Medido sobre los fixtures:
- Contexto fijo: system prompt + conocimiento ≈ 1.7K tokens, más los esquemas de las 6 herramientas ≈ 2.4K.
- Salidas de herramientas de un lote completo ≈ 7.2K tokens.
- El procesamiento del buzón (prompt del PRD §11) hace unas 20 llamadas a herramientas. En el peor caso
  secuencial son unas 20 rondas que reenvían el contexto (≈ 155K tokens de entrada y 3–6K de salida, incluido
  razonamiento). Con llamadas paralelas son unas 8 rondas (≈ 60K de entrada).

| Modelo | Precio (entrada / salida por 1M) | Lote de 6 mensajes (peor caso) | Por contrato |
|---|---|---|---|
| Gemini flash (free tier) | $0 dentro de la cuota | $0 | $0 |
| Claude Haiku 5.5 | $0.10 / $0.50 | ≈ $0.02 | ≈ $0.003 |
| Claude Sonnet 5.5 | $2 / $10 | ≈ $0.37 | ≈ $0.06 |
| Claude Opus 5.5 | $4 / $20 | ≈ $0.74 | ≈ $0.12 |

Con prompt caching del prefijo fijo (system + tools), la entrada repetida baja ~90% en Claude. Incluso sin
caché, registrar 30 contratos al mes con Opus cuesta menos de USD 4: el costo del modelo es irrelevante frente
a una hora de la analista. Lo que importa es la confiabilidad.

## 5. Estrategia de extracción

**El modelo no extrae los valores que se registran.** Los extrae un motor determinista
(`src/dominio/extraccion.py`), y el modelo orquesta, explica y propone.

| Campo | Cómo se obtiene |
|---|---|
| Tipo de documento | Encabezado: `OTROSÍ…`, `CONTRATO MARCO…`, `CONTRATO…`; cualquier otro (cotización) → `otro` |
| `id_contrato` | `No. XX-AAAA-NNN`; en un otrosí, el contrato referido. Si no existe → `AUTO-<año>-<seq>` |
| Partes / NIT / país | Primera parte con `NIT/RUC/RTN` distinta de Periferia (NIT 900123456). NIT sin puntos ni DV. País por tipo y longitud del identificador, confirmado por el domicilio. La razón social se toma del bloque de firmas (mayúsculas/minúsculas correctas) |
| Valor / moneda | Monto entre paréntesis `(COP $265.000.000)`, con separadores según el formato, **cruzado con el valor en letras** (`DOSCIENTOS SESENTA Y CINCO MILLONES` → 265000000). "No tiene un valor determinado" → `0` + `valor_indeterminado` |
| Fechas | Rango "desde … hasta …" verificado contra el plazo en meses. Si solo hay plazo: `fin = inicio + n meses − 1 día`, la misma convención de los contratos del buzón |
| Póliza | Oraciones con "póliza": tipos (`cumplimiento`, `responsabilidad_civil`…), detección de pólizas **condicionales** y, en un otrosí, la obligación de **ampliar** garantías → `pendiente` |
| Objeto | Cláusula OBJETO sin fórmulas de relleno, máximo 200 caracteres |

**Confianza calibrada por regla (no la "opina" el modelo).** Cada campo trae `evidencia` (el fragmento literal)
y `nota`:

| Confianza | Cuándo |
|---|---|
| 0.95 | Explícito y verificado por una segunda fuente (número = letras, rango = plazo, RUC = domicilio) |
| 0.90 | Explícito con una sola fuente |
| 0.85 | Fuente débil (moneda por palabra, ausencia de cláusula de póliza) |
| 0.80 | Derivado por regla con buena evidencia (moneda por país, fecha de firma acotada por la del correo) |
| 0.60 | Calculado (fecha fin por plazo) → **revisión** |
| 0.50 | Indeterminado o inconsistente (valor por demanda, letras ≠ números) → **revisión** |
| 0.00 | Ausente (`null`, nunca inventado) |

**Dónde entra el modelo.** Lee los resultados, arma las tablas, explica las diferencias y puede **proponer**
correcciones vía `contratos_validar(contrato=…)`. Todo valor propuesto que difiera del extraído baja a confianza
0.79 (`origen: "modelo"`) y por diseño cae en revisión humana. El modelo nunca puede "arreglar" un monto y
registrarlo solo.

**Clasificación (RN1–RN4)** en `src/dominio/clasificacion.py`:
- **Duplicado:** mismo id, valor y vigencia.
- **Actualización:** otrosí, mismo id con cambios, o mismo NIT + objeto con similitud ≥ 0.9 (`difflib`). Un
  documento que *no* es otrosí pero trae el mismo número con otros datos se trata como **conflicto** y va a
  revisión.
- **Nuevo:** sin coincidencia.
- **Rechazado:** sin contrato, sin partes ni objeto, u otrosí de un contrato inexistente.

Resultado sobre el buzón:

| Mensaje | Clasificación | Acción |
|---|---|---|
| msg-001 | nuevo | insertado CT-2026-015, póliza `pendiente` |
| msg-002 | nuevo | insertado CT-2026-016, `no_aplica` |
| msg-003 | actualización | CT-2026-011: fecha fin 2027-05-01→2027-11-01, valor 350000→520000, póliza → `pendiente` (el otrosí exige ampliarla) |
| msg-004 | duplicado | sin escritura |
| msg-005 | rechazado | el adjunto es una cotización |
| msg-006 | nuevo + revisión (`valor`, `fecha_fin`) | no se registra hasta confirmar. Advertencias: remitente desconocido y póliza condicional |

## 6. Regla de gobierno (propuesta de una página)

**Dueño del proceso:** la Dirección Administrativa. **Dueña del maestro:** la analista administrativa (con un
suplente nombrado, para que el proceso sobreviva a la rotación). Como no hay área legal, la custodia es
administrativa y la revisión jurídica, cuando se necesite, se contrata por caso.

1. **Canal único.** `contratos@periferia.com` es el único canal válido. Lo administra la analista
   administrativa y el agente lo procesa a diario. Los contratos enviados a buzones personales no cuentan como
   entregados.
2. **Obligación del comercial.** En los **3 días hábiles** siguientes a la firma debe enviar el PDF **firmado
   por ambas partes** de todo contrato (con o sin póliza), otrosí, acta de inicio, de suspensión o de
   terminación, y orden de servicio de contratos marco. Asunto obligatorio:
   `[CONTRATO|OTROSÍ|ACTA] <Cliente> – <N.º contrato>`. Un documento por correo.
3. **Acuse automático.** En **menos de 1 hora hábil** el agente responde al remitente con la clasificación, el
   número registrado, los campos en revisión y la ruta del archivo. Si falta algo, el acuse dice exactamente
   qué falta.
4. **Excepciones y escalamiento.**

   | Caso | Qué pasa | A quién se escala |
   |---|---|---|
   | Contrato sin firmar | Rechazado, con aviso al comercial | — |
   | Sin valor o con valor indeterminado | Registro con `valor = 0` previa confirmación de la analista | — |
   | Remitente fuera de `comerciales.json` | Se registra y la analista asigna el comercial | — |
   | Sin respuesta del comercial a los 5 días hábiles | Escalamiento | Director comercial |
   | Contrato con póliza `pendiente` a 10 días de su inicio | Alerta | Gerencia |

5. **Cierre del gap (junio–agosto 2026), en una campaña de 2 semanas.**
   1. Se extrae de facturación la lista de clientes facturados desde el 2026-05-30.
   2. Se cruza contra el maestro.
   3. Cada comercial recibe la lista de sus clientes sin contrato registrado y reenvía los PDF al buzón con el
      asunto estándar.
   4. El agente los procesa con `fuente = migracion`.
   5. Lo que no llegue al cierre de la campaña se escala a la dirección comercial.
6. **Indicador mensual.** **% de clientes facturados en el mes que tienen un contrato vigente en el maestro**,
   con meta ≥ 95%. Indicadores secundarios: días promedio entre la firma y el registro (meta ≤ 3) y número de
   pólizas `pendiente` con más de 30 días.

## 7. Decisiones y trade-offs

| Decisión | Alternativa descartada | Por qué |
|---|---|---|
| Extracción determinista con regex y confianza por regla; el LLM solo propone | Extracción 100% con el LLM (structured output) | Montos y fechas son datos financieros: el regex es reproducible, auditable (evidencia literal), gratis y testeable con golden tests. El LLM aporta en la conversación, no en el número. Con documentos más variados, el siguiente paso es un extractor LLM que pase por el mismo `validar` |
| Guard de confirmación en el grafo (por turno) + `confirmado` en la herramienta | `interrupt()` de LangGraph | La confirmación del PRD llega en lenguaje natural y con correcciones ("confirmo el valor 0 y la fecha fin…"); la interpreta mejor el modelo en el turno siguiente. El guard por turno da la misma garantía (no hay autoconfirmación) sin acoplar la API a un flujo de reanudación |
| `StateGraph` propio | `create_react_agent` prebuilt | Necesitaba el tope de iteraciones con respuesta final sin herramientas, el estado de `pendientes` y el guard antes de ejecutar |
| Gemini nativo como default gratis + adaptador multi-proveedor | Un solo proveedor de pago | El usuario no tiene llave de pago; un evaluador puede usar la suya sin tocar código |
| CSV con escritura atómica (tmp + rename) y lock | Base de datos | El PRD lo pide como no-objetivo. El CSV refleja el Excel real de SharePoint; el lock y el rename evitan corromperlo |
| Herramientas del módulo expuestas vía **MCP** (stdio + Streamable HTTP montado en la misma app) | Reescribir las herramientas en TypeScript para la plataforma | Una sola implementación (sin copias divergentes); MCP funciona con OpenCode, Claude Code, Cursor y otros; en HTTP corre en el mismo servicio de Render, sin infraestructura extra. Envoltorios TS que llamen a Python exigirían ejecutar comandos de shell, prohibido por el PRD |
| Clasificar y archivar dentro de `registrar` (también para duplicados y rechazados) | Una herramienta extra para "descartar" | Un solo punto cierra el mensaje (`procesados.json`) y la idempotencia queda centralizada |

## 8. Supuestos

- **Fecha de firma con solo mes y año** (msg-006: "en el mes de agosto de 2026"): se acota con la fecha del
  correo si cae en el mismo mes (la firma no puede ser posterior al envío), con confianza 0.8. La fecha fin
  derivada (inicio + 12 meses − 1 día = 2027-08-30) queda en revisión, y el valor que confirma el usuario
  (2027-08-31) prevalece.
- **Plazo en meses:** se usa la convención `inicio + n meses − 1 día` que siguen los contratos explícitos del
  buzón (15-ago → 14-ago).
- **Un otrosí que amplía el plazo** de un contrato con pólizas devuelve la póliza a `pendiente`, porque la
  cláusula exige ampliar su vigencia.
- **Contrato marco con póliza condicional:** se registra `requiere_poliza = true` y la condición se reporta como
  advertencia.
- **Moneda sin valor:** si no hay valor (contrato por demanda), se toma la moneda del país.
- **Archivo y ruta:** el archivo conserva la extensión de origen (`.txt` en los fixtures). En un otrosí,
  `ruta_sharepoint` sigue apuntando al contrato base y el otrosí se archiva a su lado.
- **`fecha_registro`** es la fecha de ejecución (configurable con `FECHA_REFERENCIA`). Las alertas usan la
  fecha `hoy` que da el usuario.
- **Remitente desconocido:** `comercial = "No registrado (<correo>)"`, para que quede trazable.

## 9. Cobertura

| HU | Estado | Notas / qué falta para producción |
|---|---|---|
| HU-1 Leer buzón | Hecho | Producción: conector Graph API a Exchange |
| HU-2 Extraer con confianza | Hecho | `contratos_leer_pdf` (P1) lee PDF nativos con pypdf; falta OCR para escaneos |
| HU-3 Validar y clasificar | Hecho | Producción: deduplicar por NIT contra el maestro real |
| HU-4 Registrar y archivar | Hecho | Producción: SharePoint REST/Graph en lugar de carpetas locales |
| HU-5 Alertas | Hecho | Más un anexo de contratos vencidos. Producción: envío programado semanal |
| HU-6 Errores | Hecho | Pruebas de texto vacío, fecha inválida, moneda desconocida, mensaje inexistente y argumentos inválidos |
| Front con tool calls y confirmación | Hecho | — |
| Topes de iteraciones y tokens | Hecho | Producción: rate limit por IP / autenticación SSO |
| `demo.py` sin modelo | Hecho | Determinista salvo timestamps |
| Bonus `modulo/` | Hecho | `agent.md` y `SKILL.md` generados desde las mismas fuentes (un test falla si divergen); herramientas vía **servidor MCP** (stdio local y HTTP en `/mcp`, desplegado con la app) |
| Link público | Hecho | https://agente-contratos-periferia.onrender.com (Render, Docker; `render.yaml` en la raíz del repo). Producción: plan pago sin suspensión e instancia con disco persistente |
| Acuse automático (gobierno) | Diseñado | No implementado (requiere envío de correo real) |

Pruebas: `uv run pytest` ejecuta 72 casos: golden tests de extracción por mensaje, números en letras, fechas,
flujo completo, idempotencia, fixture intacto, errores, guard de confirmación, tope de iteraciones, errores del
proveedor, esquemas aceptados por cada proveedor (sin red), API, sincronía del módulo y el servidor MCP por stdio y HTTP (con y sin clave).

## 10. Uso de IA

**Herramienta:** Claude Code (Claude Opus 5.5) como asistente de ingeniería.

**Cómo se usó.** Leyó los PDF y el PRD y propuso un plan que revisé y ajusté antes de implementar:
- el modelo debía ser configurable, gratis o de pago;
- los esquemas de las herramientas debían usar `Literal` para las opciones fijas y `Field(description=…)` como
  prompt.

Luego implementó el código y las pruebas, y validó los esquemas contra cada proveedor. Probé la aplicación con
Gemini real, revisé los archivos generados en `out/` y pedí los ajustes descritos abajo.

**Lo que se descartó o corrigió en el camino:**
- **Gemini por la capa OpenAI-compatible** → se cambió a la integración nativa, que maneja las *thought
  signatures* del tool calling multi-turno.
- **El simplificador de esquemas quitaba `default`**, y eso volvía obligatorios todos los campos opcionales en
  Gemini → se detectó con una prueba y se corrigió.
- **`interrupt()` y `create_react_agent`** → descartados (ver §7).
- **`temperature` en Claude** → no se envía, porque los modelos actuales la rechazan.
- **Errores opacos del proveedor:** en la primera prueba real el error llegaba como un genérico
  `GoogleAPIError`. Se agregó el código HTTP y el mensaje del proveedor al chat y al log del servidor. Así se
  vio que era un `503` por saturación del alias `-latest`, y se fijó una versión concreta del modelo.
- **El badge del front** mostraba el modelo configurado; ahora muestra el que reporta el proveedor en cada
  respuesta, porque la configuración solo se lee al arrancar el servidor.
- **El chat encogía cada mensaje nuevo** hasta volverlo una caja con su propio scroll → se corrigió en el CSS
  para que solo se desplace la conversación completa.

**Aprendizajes y decisiones propias:**
- **El prompt no vive solo en `agent/prompt.md`.** Las descripciones de los campos de los modelos pydantic y
  los `Literal` también son prompt: el modelo los lee en el JSON schema de cada herramienta, y de ellos
  depende que la llame bien.
- **Herramientas reutilizables sin reescribirlas.** El PRD pide el módulo con las herramientas en TypeScript
  (`tools/contratos.ts`). En lugar de duplicarlas, se expusieron con un **servidor MCP**, que plataformas como
  OpenCode, Claude Code o Cursor cargan sin importar el lenguaje en que están escritas. Así se compensa haber
  desarrollado en Python.
- **Despliegue en Render**, para que la demo no dependa de una máquina local (el PRD penaliza con −10 correrla
  en local).

## 11. Riesgos de producción y mitigación

| Riesgo | Mitigación |
|---|---|
| Contratos con formatos distintos a los fixtures (escaneos, plantillas de clientes) | OCR + extractor LLM con structured output como *fallback*, siempre pasando por `validar` y con confianza ≤ 0.79 → revisión humana. Ampliar los golden tests con cada formato nuevo |
| Free tier: cuotas (429) y uso de datos para entrenamiento | Reintentos con backoff y mensaje claro; en producción, modelo de pago con retención controlada |
| El modelo "redondea" o inventa un valor | Los valores salen de las herramientas; lo propuesto por el modelo cae en revisión; guard por turno |
| Falsos duplicados o actualizaciones por variaciones de nombre | Dedupe por NIT antes que por nombre; la similitud de objeto solo aplica con el mismo NIT; conflicto → revisión |
| El comercial no cumple la regla de gobierno | KPI de cobertura contra facturación + escalamiento; el agente registra todo lo que le llega |
| Gasto descontrolado de la llave en un link público | Topes de iteraciones, de tokens por sesión y globales; `APP_ACCESS_KEY` opcional |
| Concurrencia sobre el maestro | Lock + escritura atómica; en producción, SharePoint con control de versiones (ETag) |
| Pérdida de `out/` en Render (disco efímero) | Aceptable para la demo; en producción el maestro vive en SharePoint |

## Anexo · Dependencias y por qué

| Dependencia | Para qué | Por qué esta y no otra |
|---|---|---|
| `langgraph` | Ciclo del agente: grafo agente ⇄ herramientas, estado por sesión (checkpointer) | Control explícito del tope de iteraciones, del guard de confirmación y del estado `pendientes` |
| `langchain-core` | Tipos de mensajes y `bind_tools` comunes a todos los proveedores | Es la interfaz neutral sobre la que se implementa el adaptador propio |
| `langchain-google-genai` | Gemini nativo (proveedor gratis por defecto) | Maneja las *thought signatures* del tool calling multi-turno; la capa OpenAI-compatible puede perderlas |
| `langchain-openai` | Groq, OpenRouter, Ollama, OpenAI y cualquier endpoint OpenAI-compatible | Una sola implementación cubre 5 proveedores vía `base_url` |
| `langchain-anthropic` | Claude nativo | Soporte de `output_config.effort` y del fallback server-side |
| `pydantic` | Esquemas de herramientas y validación (equivalente a `zod`) | `Literal` → `enum` y `Field(description)` → prompt en el JSON schema; valida antes de ejecutar |
| `pydantic-settings` | Configuración tipada desde `.env` | Falla al arrancar con un mensaje claro si la configuración es inválida; la llave queda como `SecretStr` |
| `fastapi` + `uvicorn` | API HTTP, front estático y montaje del endpoint MCP | Validación de entrada con los mismos modelos pydantic; un solo proceso sirve todo |
| `mcp` | Servidor MCP del módulo (bonus): stdio y Streamable HTTP | SDK oficial del protocolo; reutiliza las mismas herramientas sin reescribirlas |
| `pypdf` | `contratos_leer_pdf` (P1): texto de PDF nativos | Pura Python, sin binarios del sistema; se importa solo si se necesita |
| `pytest`, `httpx` (dev) | Pruebas y cliente HTTP de prueba | Estándar; no van en la imagen de producción (`--no-dev`) |

No se usan base de datos, colas ni frameworks de front: el PRD los deja fuera de alcance y agregarlos solo
sumaría superficie de fallo.

