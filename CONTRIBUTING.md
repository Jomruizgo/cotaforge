# Contributing to CotaForge

Thanks for helping. CotaForge is an alpha, one-maintainer project, so these rules are short and meant to save everybody's time.

By contributing you agree that your work is released under the [MIT License](LICENSE). Be kind: see [Code of conduct](#code-of-conduct) below. AI-assisted work is welcome under the rules in [AI_POLICY.md](AI_POLICY.md).

## Issue first, when it is not small

- **Features and any change over about 200 lines:** open an issue first and wait for the maintainer's OK (the `status:approved` label) before you start. Otherwise the work may be thrown away.
- **Small fixes and docs:** go straight to a pull request.
- **Bugs:** an issue with the bug template helps even if you also send the fix.

## Pull requests

- Target the `develop` branch (never `main`; releases are cut from `develop`). Branch from `develop` with a name like `fix/short-description`.
- Keep a PR focused and small: about **400 changed lines** is the guideline. Bigger only by agreement in the issue.
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat(scope): ...`, `fix: ...`, `docs: ...`, `chore: ...`, `test: ...`, `refactor: ...`, `perf: ...`, `ci: ...`. Code, comments, commits and PRs are in English. The user manual has an English and a Spanish file.
- Fill in the PR template with the real test results, and say if AI helped (one sentence).
- Use any AI tool you like, but keep its context out of the repository: no `CLAUDE.md`, `AGENTS.md`, `.claude/`, `.cursor/`, prompt files or agent notes for developing the code. Keep them in your own folder outside the repository. Only the product's own files are allowed (`templates/`, `plugin/`). A unit test fails if anything else appears.
- Do not name commercial CAD software anywhere in the repository: a unit test fails if you do. Write "commercial CAD software". Parasolid, `x_t` and `x_b` are fine.

## Running the tests

You need Python 3.12+.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e "kernel[dev]" -e "mcp-server[dev]"

cd kernel && python -m pytest tests/unit && cd ..
cd mcp-server && python -m pytest tests/unit && cd ..
python -m ruff check kernel mcp-server
```

The unit suites need neither FreeCAD nor Claude Code (they use a fake CAD) and are what CI runs. Tests marked `freecad`, the `integration` and `acceptance` folders need FreeCAD 1.1.x: set `COTAFORGE_FREECAD_ROOT` if it is not in its default folder. If you cannot run them, say so in the PR. Tests must never touch your real profile: use a temporary data directory.

Two housekeeping scripts, if your change needs them:

- You edited `templates/design-space`: run `python mcp-server/scripts/sync_template.py`, then `python mcp-server/scripts/conduct_history.py` (a unit test checks both).
- You changed the dependencies of the installer: regenerate `THIRD_PARTY_NOTICES.md` with `packaging/windows/third_party_notices.py` (the build does it for the installer on its own).

## Labels

Maintainers apply them; you can suggest one in the issue.

| Group | Labels |
|---|---|
| Type | `type:bug`, `type:feature`, `type:docs`, `type:chore` |
| Status | `status:needs-info` (waiting for you), `status:approved` (you can start) |
| Welcome | `good first issue`, `help wanted` |

## Code of conduct

Be respectful. We are building something together.

- Critique code, not people.
- Be constructive in reviews.
- Welcome newcomers.

The maintainer moderates issues, pull requests and discussions, and may hide comments, lock threads or block accounts that break these rules.

## Where things are

`kernel/` is the logic and the CAD adapters, `mcp-server/` the MCP server and the `cotaforge` command, `plugin/` the Claude Code plugin, `templates/` the design-space files, `packaging/windows/` the installer. The README has the map; the manual has the user view.
