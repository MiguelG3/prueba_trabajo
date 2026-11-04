# Módulo reutilizable (bonus §9.4)

El agente empaquetado para integrarse a otras plataformas de agentes, **sin depender del servidor web**.
Son las mismas tres piezas que usa la aplicación (no hay copias divergentes):

| Pieza | Archivo | Fuente |
|---|---|---|
| Agente | `agent.md` (frontmatter `description`, `mode: primary`, `permission {edit: deny, bash: deny}`) | `agent/prompt.md`, generado por `scripts/build_modulo.py` |
| Conocimiento | `skill/registro-contratos/SKILL.md` | `src/knowledge/registro-contratos.md`, generado |
| Herramientas | `tools/contratos.py` (import directo en Python) y `mcp_server.py` (cualquier plataforma vía MCP) | Re-exportan `src/tools/contratos.py` |

`tests/test_modulo.py` falla si `agent.md` o `SKILL.md` se desvían de sus fuentes.

## Herramientas vía MCP

Las plataformas de agentes cargan herramientas externas con el **Model Context Protocol**. `mcp_server.py`
publica las 6 herramientas con el mismo JSON schema (pydantic) y la misma ejecución que la app:

| MCP | En la app | Anotación |
|---|---|---|
| `leer_buzon`, `extraer`, `validar`, `leer_pdf` | `contratos_*` | solo lectura |
| `registrar`, `alertas` | `contratos_*` | escriben en `out/` (idempotentes, nunca borran) |

Si registras el servidor con el nombre `contratos`, el cliente las muestra como `contratos_leer_buzon`,
`contratos_registrar`…: el mismo contrato `<archivo>_<export>` del PRD.

### Opción A — Local (stdio): el cliente lanza el proceso

Requiere el repositorio clonado y `uv`. Las herramientas leen `fixtures/` y escriben `out/` de esa carpeta.

**OpenCode** (`opencode.json`):
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "contratos": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/ruta/a/reto-02-solucion", "python", "-m", "modulo.mcp_server"],
      "enabled": true
    }
  }
}
```

**Claude Code**:
```bash
claude mcp add contratos -- uv run --directory /ruta/a/reto-02-solucion python -m modulo.mcp_server
```

### Opción B — Remoto (HTTP): el servidor desplegado en Render

La misma app web expone el endpoint MCP en `/mcp`, protegido con la `APP_ACCESS_KEY`. No hay que clonar nada:

**OpenCode**:
```json
{
  "mcp": {
    "contratos": {
      "type": "remote",
      "url": "https://<servicio>.onrender.com/mcp",
      "headers": { "Authorization": "Bearer <APP_ACCESS_KEY>" }
    }
  }
}
```

**Claude Code**:
```bash
claude mcp add --transport http contratos https://<servicio>.onrender.com/mcp --header "Authorization: Bearer <APP_ACCESS_KEY>"
```

### Agente y skill en la plataforma

Copia `agent.md` y `skill/registro-contratos/SKILL.md` a las carpetas de agentes y skills de la plataforma
(por ejemplo, en OpenCode `.opencode/agent/contratos.md` y `.opencode/skill/registro-contratos/SKILL.md`).
El servidor MCP también envía el prompt y el conocimiento como `instructions` al conectarse.

### Diferencias frente a la app web

- **El modelo es el de la plataforma cliente**, no el de la app: vía MCP no se gasta la `LLM_API_KEY` del servidor.
- **La confirmación humana** depende de dos capas:
  1. La regla de la herramienta, que es la misma: `registrar` rechaza los campos en revisión sin `confirmado: true`.
  2. Los permisos del cliente: `registrar` no es de solo lectura, así que los clientes piden aprobación antes de ejecutarla.

  El guard por turno (que impide al modelo autoconfirmarse) vive en el grafo de la app web, no en el servidor MCP.
- **En remoto, el estado `out/` es el del servidor de Render**: es compartido con el chat web.
