# Agente de registro de contratos vigentes — Reto 02 · Periferia IT Group

Agente conversacional que actúa como **punto único de recepción** de contratos: lee el buzón, extrae los datos
de cada contrato con confianza por campo, detecta nuevos / actualizaciones / duplicados / rechazados, registra
en el maestro (SharePoint simulado), pide confirmación humana para lo dudoso y genera alertas de vencimiento y
pólizas.

**Stack:** Python 3.12 · LangGraph · FastAPI · pydantic (equivalente a zod) · front HTML plano.
El modelo es **configurable, gratis o de pago** (Gemini, Groq, OpenRouter, Ollama, OpenAI, Claude...).

> El PRD pide TypeScript; por requerimiento se resolvió en **Python + LangGraph** respetando el mismo contrato:
> nombres de herramientas (`contratos_*`), respuestas `{ ok, data } | { ok: false, error }`, rutas de `out/`,
> API HTTP y `demo` sin modelo. Ver [SOLUCION.md](SOLUCION.md) §0.

## Arranque local (un comando)

```bash
cp .env.example .env        # pon tu LLM_API_KEY (Gemini es gratis: https://aistudio.google.com/apikey)
uv sync && uv run dev       # front + backend en http://localhost:8000
```

Requisitos: [uv](https://docs.astral.sh/uv/) (instala Python 3.12 si hace falta). Sin llave el servidor arranca
igual: `/api/health` y el chat explican qué falta. Para correr **sin llave**, usa Ollama local
(`LLM_PROVIDER=ollama`).

## Verificación sin modelo

```bash
uv run demo.py              # procesa los 6 mensajes llamando directamente a las herramientas
uv run pytest               # 72 pruebas: golden por mensaje, errores, idempotencia, guard de confirmación, API, MCP
```

`demo.py` limpia `out/`, fija `hoy = 2026-09-03` y muestra: clasificación y acción por mensaje, msg-006 **sin
registrar** en la primera pasada, la segunda pasada con `confirmado: true`, la idempotencia de msg-001 y el
resumen de alertas.

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` · `groq` · `openrouter` · `ollama` (gratis) · `openai` · `anthropic` · `openai_compatible` (pago) |
| `LLM_API_KEY` | — | Llave del proveedor (no requerida para Ollama) |
| `LLM_MODEL` | según preset | Cualquier modelo del proveedor |
| `LLM_BASE_URL` | según preset | Obligatoria para `openai_compatible` (Azure, Mistral, vLLM...) |
| `LLM_EFFORT` / `LLM_ANTHROPIC_FALLBACK` | — / `true` | Solo Claude |
| `LLM_TIMEOUT_S` / `LLM_MAX_RETRIES` | `60` / `3` | Timeout y reintentos con backoff ante 429 |
| `MAX_ITERACIONES` | `25` | Tope herramienta→modelo por turno |
| `MAX_TOKENS_SESION` / `MAX_TOKENS_GLOBAL` | `300000` / `3000000` | Topes de gasto |
| `APP_ACCESS_KEY` | — | Si se define, el chat exige el header `X-Access-Key` (campo "Clave de acceso" del front) |
| `FECHA_REFERENCIA` | hoy | Fija `fecha_registro` (demos deterministas) |

`.env.example` trae un bloque listo por proveedor.

## API

| Método | Ruta | Cuerpo / respuesta |
|---|---|---|
| `POST` | `/api/chat` | `{ sessionId, message }` → `{ reply, toolCalls[{nombre, args, ok, resumen, resultado}], needsConfirmation, pendientes[], tokensSesion, modelo }` (`modelo` = el que respondió, según el proveedor) |
| `GET` | `/api/sessions/:id` | Historial completo de la sesión (usuario, agente, herramientas) |
| `GET` | `/api/health` | `{ ok, provider, model, tier }` — nunca expone la llave |
| `POST` | `/api/reset` | Limpia `out/` y las sesiones (para repetir la demo) |
| `GET` | `/api/out/:ruta` | Lectura de archivos generados (`alertas.md`, maestro, historial, log) |
| `POST` | `/mcp` | Servidor **MCP** (Streamable HTTP) con las mismas herramientas, para conectar OpenCode, Claude Code, Cursor… Ver [modulo/README.md](modulo/README.md) |

## Link de prueba

- URL: `https://<tu-servicio>.onrender.com` _(completar tras el despliegue)_
- Clave de acceso: _(la de `APP_ACCESS_KEY`, si se definió)_

### Despliegue en Render

El repositorio tiene esta carpeta como subdirectorio; el `render.yaml` está en la **raíz del repo** y apunta
aquí (`rootDir`, `dockerfilePath`, `dockerContext`).

1. En Render: **New → Blueprint** → conecta GitHub y selecciona el repo.
2. Ingresa los secrets `LLM_API_KEY` y `APP_ACCESS_KEY` cuando los pida.
3. El plan gratis se duerme tras 15 min sin tráfico: abre el link ~1 min antes de la defensa.

El mismo servicio expone el servidor MCP en `https://<tu-servicio>.onrender.com/mcp` (con la misma
`APP_ACCESS_KEY` en `Authorization: Bearer …`). Configuración de clientes en [modulo/README.md](modulo/README.md).

> `out/` vive en el disco efímero del contenedor: se reinicia con cada despliegue (suficiente para la demo;
> en producción sería SharePoint).

## Prompt de ejemplo

```
Procesa el buzón de contratos con fecha de hoy 2026-09-03. Registra lo que esté limpio, muéstrame lo que
requiere revisión campo por campo y termina con el reporte de alertas. No registres nada dudoso sin preguntarme.
```

Luego: `confirmo el valor 0 y la fecha fin 2027-08-31` → msg-006 queda registrado.

## Estructura

```
agent/prompt.md                  comportamiento (system prompt)
src/knowledge/                   conocimiento del proceso (reglas de negocio)
src/tools/                       ejecución: contrato Tool + esquemas pydantic + herramientas contratos_*
src/dominio/                     lógica pura: extracción, números/fechas en español, clasificación, maestro CSV
src/graph.py                     ciclo del agente (LangGraph): tope de iteraciones, guard de confirmación
src/llm/                         adaptador propio + presets de proveedores (gratis / pago)
src/server.py                    API FastAPI + front estático
web/                             chat (tool calls visibles, banner de confirmación)
modulo/                          bonus: agente empaquetado + servidor MCP (stdio y HTTP en /mcp)
demo.py · tests/                 verificación sin modelo
```
