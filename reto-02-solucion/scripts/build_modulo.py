"""Genera modulo/ desde las fuentes canónicas (agent/prompt.md y src/knowledge/). Uso: uv run python scripts/build_modulo.py

El test tests/test_modulo.py falla si modulo/ diverge de las fuentes: no hay copias que mantener a mano.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

AGENT_FRONTMATTER = """---
description: Punto único de recepción de contratos de Periferia — clasifica el buzón, extrae datos con confianza, registra en el maestro con confirmación humana y genera alertas de vencimiento y pólizas.
mode: primary
permission:
  edit: deny
  bash: deny
---

"""

SKILL_FRONTMATTER = """---
name: registro-contratos
description: Reglas del registro de contratos vigentes de Periferia (esquema del maestro, clasificación nuevo/actualización/duplicado/rechazado, umbral de confianza, pólizas y alertas). Úsala al procesar contratos u otrosíes del buzón.
---

"""


def generar() -> dict[Path, str]:
    return {
        RAIZ / "modulo" / "agent.md": AGENT_FRONTMATTER + (RAIZ / "agent" / "prompt.md").read_text(encoding="utf-8"),
        RAIZ / "modulo" / "skill" / "registro-contratos" / "SKILL.md":
            SKILL_FRONTMATTER + (RAIZ / "src" / "knowledge" / "registro-contratos.md").read_text(encoding="utf-8"),
    }


if __name__ == "__main__":
    for ruta, contenido in generar().items():
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(contenido, encoding="utf-8")
        print(f"escrito {ruta.relative_to(RAIZ)}")
