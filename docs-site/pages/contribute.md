# Contribute

- [CONTRIBUTING](https://github.com/Jomruizgo/cotaforge/blob/main/CONTRIBUTING.md): how to propose a change and run the tests.
- [AI policy](https://github.com/Jomruizgo/cotaforge/blob/main/AI_POLICY.md): rules for work made with AI.
- [Security](https://github.com/Jomruizgo/cotaforge/blob/main/SECURITY.md): report a vulnerability in private.
- [License](https://github.com/Jomruizgo/cotaforge/blob/main/LICENSE): MIT. Bundled software has its own licenses: [THIRD_PARTY_NOTICES](https://github.com/Jomruizgo/cotaforge/blob/main/THIRD_PARTY_NOTICES.md).

Use any AI tool you like, but keep its context out of the repository: no `CLAUDE.md`, `AGENTS.md`, `.claude/` or prompt files.

## Build this site

```bash
cd docs-site
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdocs serve
```

The manual pages include `docs/user-manual.md` and `docs/manual-usuario.md` at build time. The installer carries the same files.
