# CotaForge

**English** · [Español](README.es.md)

Mechanical design in FreeCAD, driven from Claude Code. The agent builds parts, assemblies and shop drawings in small, measured steps. You can interrupt it, undo any step, and save and resume the native file.

**Early development version.** Expect errors and changes. It works only with Claude Code and FreeCAD 1.1.x. Windows is supported; Linux works from source.

Website: <https://jomruizgo.github.io/cotaforge/>

## Install

You need [FreeCAD 1.1](https://www.freecad.org/downloads.php) and [Claude Code](https://claude.com/claude-code), signed in. On Windows, Claude Code also needs Git for Windows.

**Windows installer.** Download [cotaforge-setup.exe](https://github.com/Jomruizgo/cotaforge/releases/latest/download/cotaforge-setup.exe) and run it. For another version, use the [Releases](https://github.com/Jomruizgo/cotaforge/releases) page. The installer is not signed: Windows shows "Windows protected your PC". Click **More info**, then **Run anyway**.

**From source.** You need Python 3.12, Git and FreeCAD 1.1.

```bash
git clone https://github.com/Jomruizgo/cotaforge.git
cd cotaforge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e kernel -e mcp-server
cotaforge init ~/my-designs
cd ~/my-designs
claude --plugin-dir /path/to/cotaforge/plugin
```

Use an editable install (`-e`): `cotaforge init` finds the `plugin/` folder through it. If FreeCAD is not in its default folder, pass `--freecad-root`. Add `--visible` to watch the part grow in the FreeCAD window.

## First use

With the installer: Start menu > **Create new project (CotaForge)**, choose an empty folder and open it in Claude Code. Then:

```text
/cotaforge:plan a 100 x 60 x 8 mm steel plate with four 9 mm through holes
```

## Documentation

- [User manual](docs/user-manual.md), also in Spanish: [manual de usuario](docs/manual-usuario.md).
- [Website](https://jomruizgo.github.io/cotaforge/).

## What is here

| Folder | What it is |
|---|---|
| `kernel/` | Python package: CAD adapters, step executor, measurement, constraints, memory. |
| `mcp-server/` | The MCP server `cad` and the `cotaforge` command. |
| `plugin/` | The Claude Code plugin: commands and agents. |
| `templates/` | The design-space template that `cotaforge init` copies. |
| `packaging/windows/` | The Windows installer. |

## Limits

- Only FreeCAD 1.1.x and only Claude Code.
- Without the FreeCAD window there is no 3D capture and no selection by pointing.
- `check` compares the part with the material, process and size you declared. It does not verify tolerances or stress.
- The agent's judgment is not verified.
- `run_native` is not a sandbox.

## Feedback

[Open an issue](https://github.com/Jomruizgo/cotaforge/issues/new/choose). Attach the CotaForge, Claude Code and FreeCAD versions, what you asked and the exact error. Never attach a customer's file.

## Disclaimer

Provided as is under the MIT license. The authors take no responsibility for misuse, for errors the tool makes, or for the CAD software or the AI model it uses. You review the design and the drawings, and you are responsible for what goes to production.

## Contributing and license

[CONTRIBUTING.md](CONTRIBUTING.md), [AI_POLICY.md](AI_POLICY.md), [SECURITY.md](SECURITY.md). MIT License: [LICENSE](LICENSE). Bundled software: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
