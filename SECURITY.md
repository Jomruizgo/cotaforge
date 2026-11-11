# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting:
**Security** tab of this repository > **Report a vulnerability**.

Include what you found, how to reproduce it, and the version (`cotaforge version`). This is a one-person project in
alpha: expect an acknowledgement within about a week, and a fix or a decision as soon as the work allows.

## What is in scope

cotaforge runs code on your machine on behalf of an AI agent, so these matter most:

- the `run_native` escape hatch and its confirmation hook (the scan is a heuristic, not a sandbox: see the README);
- the MCP server and the files `cotaforge init` writes (`.mcp.json`, `.claude/settings.local.json`);
- the Windows installer and its scripts.

Problems in FreeCAD, Claude Code or a Python dependency belong to those projects.

## Supported versions

Only the latest release.
