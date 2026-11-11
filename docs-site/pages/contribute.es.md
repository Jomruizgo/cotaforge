# Contribuir

- [CONTRIBUTING](https://github.com/Jomruizgo/cotaforge/blob/main/CONTRIBUTING.md): cómo proponer un cambio y correr las pruebas (en inglés).
- [Política de IA](https://github.com/Jomruizgo/cotaforge/blob/main/AI_POLICY.md): reglas para el trabajo hecho con IA (en inglés).
- [Seguridad](https://github.com/Jomruizgo/cotaforge/blob/main/SECURITY.md): reporta una vulnerabilidad en privado.
- [Licencia](https://github.com/Jomruizgo/cotaforge/blob/main/LICENSE): MIT. El software incluido tiene sus propias licencias: [THIRD_PARTY_NOTICES](https://github.com/Jomruizgo/cotaforge/blob/main/THIRD_PARTY_NOTICES.md).

Usa la herramienta de IA que quieras, pero deja su contexto fuera del repositorio: sin `CLAUDE.md`, `AGENTS.md`, `.claude/` ni archivos de prompts.

## Construir este sitio

```bash
cd docs-site
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdocs serve
```

Las páginas del manual incluyen `docs/user-manual.md` y `docs/manual-usuario.md` al construir. El instalador lleva los mismos archivos.
