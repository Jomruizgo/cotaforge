# ruff: noqa: E501
"""The register of a new design space (``assumptions-and-decisions.md``) in the engineer's language.

The English text is the file of the template (``templates/design-space``); a project in Spanish gets this one instead,
because the register is something the engineer reads and edits.
"""

SPANISH = """# Supuestos y decisiones

Registro vivo del diseño. El agente lo mantiene al día y `/cotaforge:resume` lo lee. Se escribe en el idioma del proyecto. Cada línea lleva su fuente: catálogo (norma), catálogo del usuario (proveedor, fecha), web (URL, fecha), supuesto o el script de `calculations/` que produjo el número.

## Supuestos
<!-- Dato | valor | fuente o "supuesto" | confirmado por el ingeniero (sí/no) -->

## Decisiones
<!-- Decisión | alternativas descartadas | porqué | fecha -->

## Cálculos
<!-- Qué se calculó | resultado | script en calculations/ | supuestos que usa -->
"""
