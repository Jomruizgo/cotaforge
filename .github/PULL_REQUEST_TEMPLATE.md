## What and why

<!-- One or two sentences. Link the issue (`Closes #123`) if there is one; features and changes over ~200 lines need an approved issue first. -->

## How I checked it

<!-- The real result: which suites you ran and what they printed. Say what you could NOT check (for example, FreeCAD not available). -->

- [ ] `kernel` unit tests: `cd kernel && python -m pytest tests/unit`
- [ ] `mcp-server` unit tests: `cd mcp-server && python -m pytest tests/unit`

## Checklist

- [ ] The PR targets `develop` and is focused (about 400 changed lines at most, unless agreed).
- [ ] Commits follow Conventional Commits.
- [ ] I read every changed line and removed anything invented or unrelated.
- [ ] AI help, if any, is disclosed in one sentence here (see AI_POLICY.md): <!-- e.g. "Written with Claude Code; I read every line and ran both suites." / "No AI help." -->
