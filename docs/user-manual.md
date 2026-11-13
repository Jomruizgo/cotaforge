# CotaForge User Manual

CotaForge lets you design 3D mechanical parts by asking Claude Code in plain language. Claude does not draw by eye: it drives real CAD software (FreeCAD) through an MCP server, and every measurement it reports comes from that software.

This manual assumes you already use Claude Code (terminal, prompts, permissions, plugins, MCP servers). There is a [glossary](#13-glossary) of the product's own terms at the end.

## Contents

1. [What it is and what it is not](#1-what-it-is-and-what-it-is-not)
2. [Declare instead of drawing](#2-declare-instead-of-drawing)
3. [Requirements](#3-requirements)
4. [Step-by-step installation](#4-step-by-step-installation)
5. [Create your design space](#5-create-your-design-space)
6. [Your first design](#6-your-first-design)
7. [How the agent works](#7-how-the-agent-works)
8. [Save and resume](#8-save-and-resume)
9. [Materials, processes and constraints](#9-materials-processes-and-constraints)
10. [Memory: decisions and corrections](#10-memory-decisions-and-corrections)
11. [`run_native`, the emergency exit](#11-run_native-the-emergency-exit)
12. [Limits](#12-limits)
13. [Glossary](#13-glossary)
14. [Common problems](#14-common-problems)
15. [How to give feedback](#15-how-to-give-feedback)
16. [Quick reference sheet](#16-quick-reference-sheet)

---

## 1. What it is and what it is not

CotaForge is three pieces installed on your machine:

- An **MCP server** called `cad`. It connects Claude Code to the CAD software and exposes a small set of tools, each with several actions (section 7).
- A **Claude Code plugin** called `cotaforge`, with the commands `/cotaforge:plan`, `/cotaforge:resume`, `/cotaforge:fix`, `/cotaforge:declare`, `/cotaforge:review` and `/cotaforge:friction`, and the agents `reviewer` (reviews the design), `fabricator` (reviews drawings) and `drafter` (makes drawings).
- A **design space**: a folder of yours, created with the "Create new project (CotaForge)" shortcut or with `cotaforge init`. It holds the agent's instructions, the permissions and your project memory.

**How it works.** The agent builds the part in small steps called *operations* (a sketch, an extrusion, a hole). After each one the CAD software returns measured facts: volume, bounding box, validity. The agent reads them before the next step. If a step fails, nothing changes. Every step can be undone.

**No number comes from the language model.** Volumes, measurements and counts come from the CAD software. The `CLAUDE.md` in your design space forbids the agent to estimate figures, and it must say what it cannot verify.

**What it is not.**

- It does not replace your engineering judgment. It checks geometry and what you declared. The reviewer agent separates what it verified from what is judgment (section 9).
- It is not new CAD software. FreeCAD builds the geometry.
- It is not a sandbox. `run_native` runs real code inside FreeCAD (section 11).
- It works only with FreeCAD 1.1.x and from Claude Code (terminal or the Code tab of the desktop app). See [Limits](#12-limits).

---

## 2. Declare instead of drawing

You declare *what* the part must be. The agent decides *how* to build it.

You declare:

- The intent and the dimensions.
- The material and the permitted manufacturing processes.
- The limits of those processes (machine size, maximum lathe diameter).
- Decisions and corrections to remember (section 10).

**Procedural** (avoid): "Draw a 60 by 40 rectangle on XY. Extrude it 10. Draw an 8 circle on the top face. Cut it through."

**Declarative** (use):

> 60 x 40 x 10 mm plate in 6061 aluminum, machined on the shop's 3-axis milling machine (working volume 100 x 80 x 60 mm), with a Ø8 through hole centered on the top face. Verify with `check` before exporting.

The first prompt only describes gestures and gives nothing to verify against. The second lets `check` compare the measured part with the material, process and limits you declared. Limits belong to a process: the size that fits in a mill is `build_volume_mm`; the maximum diameter (`max_diameter_mm`) belongs to a lathe (`turning`).

| You declare | Recorded with | Effect |
|---|---|---|
| Dimensions and intent | `op` (`intent` field) | Each step stores a "why" line and returns measured volume and box. |
| Material | `memory` (`save`, type `material`) and `doc` (`set_material`) | Assigned to the document. |
| Permitted processes | `memory` (`save`, type `process`) and `doc` (`set_route`) | The process route is assigned to the document. |
| Limits | process capabilities in `memory` | `check` compares the part against them. |
| Decisions and corrections | `memory` (`save`) | Loaded in future sessions. |
| "Does it meet what I declared?" | `check` | Reports, and blocks export on violations. |

**Closed world.** If you declare at least one material, only declared materials can be assigned. The same goes for processes. If you declare nothing of a type, that type does not restrict. With "Aluminio 6061" declared, asking for "Titanio" returns:

```
error: outside_declared
material 'Titanio' is not declared; declared: ['Aluminio 6061']
```

**The plan is readable.** What the agent builds is a list of operations with their parameters. `inspect` with `action="tree"` shows it and `doc` with `save` stores it next to the file. There is no hidden script.

---

## 3. Requirements

| Requirement | Detail |
|---|---|
| Operating system | 64-bit Windows 10 or 11 with the `.exe` installer (4.1). Linux from a clone of the repository (4.2). |
| Python | 3.12 or higher, only for the clone. The Windows installer brings its own. |
| FreeCAD | Version **1.1.x**. Download: <https://www.freecad.org/downloads.php>. The installer does not include it. |
| Claude Code | Installed and signed in (desktop app, Code tab, or `claude` in a terminal). CotaForge does not manage your account. |
| Git | On Windows, Claude Code runs hooks through Git Bash, so install Git for Windows. |

### How FreeCAD is found

CotaForge needs the FreeCAD **root folder**, the one that contains the command-line executable.

- **Windows:** `bin\FreeCADCmd.exe` inside the root. If you configure nothing, it looks in the newest `C:\Program Files\FreeCAD*` folder and then in `%LOCALAPPDATA%\Programs\FreeCAD*`. The official installer's `C:\Program Files\FreeCAD 1.1` is found without configuration.
- **Linux:** `usr/bin/freecadcmd` inside the root. By default it looks in `~/Applications/freecad-1.1.3/squashfs-root`, the folder created by `./<file>.AppImage --appimage-extract`.

For any other location, use `--freecad-root` in `cotaforge init` or set `COTAFORGE_FREECAD_ROOT`. Priority: the argument, then the variable, then the default locations.

---

## 4. Step-by-step installation

If you only design, use the Windows installer (4.1). The clone (4.2) is for development or Linux.

### 4.1 With the Windows installer

The installer installs per user, **without administrator permissions**, in `%LOCALAPPDATA%\Programs\cotaforge`. It brings its own Python and dependencies.

**1. Download** the latest installer: <https://github.com/Jomruizgo/cotaforge/releases/latest/download/cotaforge-setup.exe>. For another version, see the [Releases page](https://github.com/Jomruizgo/cotaforge/releases).

**2. Run it.** The installer is not signed with a paid certificate, so Windows SmartScreen shows "Windows protected your PC". Click **More info**, then **Run anyway**.

**3. Follow the wizard.** It looks for two programs it does not include:

| Program | What the installer does |
|---|---|
| **FreeCAD 1.1** | Looks in `C:\Program Files\FreeCAD*`, `%LOCALAPPDATA%\Programs\FreeCAD*` and the registry. If it finds nothing, it opens the download page if you ask, lets you pick the folder (the one with `bin\FreeCADCmd.exe`) or lets you continue without it. If you install FreeCAD later in its usual folder, CotaForge finds it. |
| **Claude Code** | Looks for `claude.exe` in the `PATH` and in `%USERPROFILE%\.local\bin`. If missing, it offers to launch the official installer or to continue without it. You sign in yourself. |

At the end it registers the `cotaforge` plugin in Claude Code, if Claude Code is installed. Otherwise the plugin is registered the first time you use a shortcut.

**4. Use the Start-menu shortcuts** (folder *CotaForge*):

| Shortcut | What it does |
|---|---|
| **Create new project (CotaForge)** | Asks for a folder (you can create it there), prepares the design space with the FreeCAD window visible, registers the plugin if missing and tells you how to open it. |
| **Open existing project (CotaForge)** | Asks for the folder of an existing project and refreshes its configuration, like `cotaforge init` without `--force`. Your memory is not touched. Your `CLAUDE.md` is updated only if you never edited it. If the folder is not a CotaForge project, it changes nothing. |
| **CotaForge manual** | Opens this manual. |
| **Uninstall CotaForge** | Removes the program. |

**5. Open the project in Claude Code:**

- **Desktop app:** **Code** tab, choose the project folder. Type `/cotaforge:` to see the commands.
- **Terminal:** `cd` into the project folder and run `claude`.

You do not need `--plugin-dir`: the plugin is registered in your Claude Code user configuration.

**Updating.** Run the new `.exe` over the old one. It replaces the program and the plugin and never touches your projects. Save your work in FreeCAD first. A Claude session that used CotaForge keeps its CAD server running, even after you close the window, and Windows cannot replace a running program. If the installer finds that server, it asks whether to close it. Answer yes to close only the CotaForge processes, then reconnect the `cad` server with `/mcp` or open a new session. Answer no, close the Claude app completely (also from the tray icon) and run the installer again.

**Uninstalling.** Use *Settings > Apps* or the shortcut. It removes the program and the plugin registration. **Your projects and your memory (`%USERPROFILE%\.cotaforge`) stay.**

**Checking the installation:**

```powershell
& "$env:LOCALAPPDATA\Programs\cotaforge\python\python.exe" -m cotaforge_mcp.cli version
& "$env:LOCALAPPDATA\Programs\cotaforge\python\python.exe" -m cotaforge_mcp.cli plugin status
```

What the installer detected (FreeCAD folder, `claude` path) is in `%LOCALAPPDATA%\Programs\cotaforge\install.json`.

The installer does not modify the `PATH`. For the `cotaforge` command in a terminal, use the `python.exe -m cotaforge_mcp.cli ...` form above (for example `... init <folder>`, `... plugin register`).

### 4.2 From a repository clone

CotaForge is not on PyPI. It has two Python packages: `kernel` and `mcp-server`.

```bash
git clone https://github.com/Jomruizgo/cotaforge.git
cd cotaforge
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e kernel -e mcp-server
```

On Windows (PowerShell). Clone into a short path such as `C:\dev`: a path of 210 characters or more is rejected (section 14).

```powershell
git clone https://github.com/Jomruizgo/cotaforge.git
cd cotaforge
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e kernel -e mcp-server
```

Use `-e`: `cotaforge init` finds the clone's `plugin/` folder only for an editable install. Otherwise pass `--plugin-dir` (section 5).

The clone is also a Claude Code marketplace. Register the plugin once so you do not need `--plugin-dir`:

```bash
cotaforge plugin register
```

`cotaforge plugin status` shows whether it is registered and `cotaforge plugin unregister` removes it. The registration points to the clone: if you move it, register again.

The install adds two commands to the virtual environment: `cotaforge` (`init`, `plugin`, `version`) and `cotaforge-mcp` (the MCP server, launched by Claude Code). Check it:

```bash
cotaforge version
```

For the tests, install the extras: `python -m pip install -e "kernel[dev]" -e "mcp-server[dev]"`. With FreeCAD installed, `cd mcp-server && python -m pytest tests/acceptance` runs a walkthrough against real FreeCAD (set `COTAFORGE_FREECAD_ROOT` first if FreeCAD is not in a default location). To build the Windows installer: `packaging\windows\build.ps1` (see `packaging/windows/`).

**The virtual environment (or the `.exe` installation) is pinned in the files `cotaforge init` writes.** If you move, delete or recreate it, run `init` again in your design space. With the installer, the **Open existing project (CotaForge)** shortcut does that.

---

## 5. Create your design space

A **design space** is a folder of yours (the command creates it if missing) that holds your parts, your project memory and your Claude Code session.

**With the Windows installer**, use **Create new project (CotaForge)**. It does what `cotaforge init --visible` does in the folder you choose. **Open existing project (CotaForge)** repeats `init` without `--force`, for example after an update.

**From a terminal:**

```bash
cotaforge init ~/design
```

```
Espacio de diseño listo en /home/you/design
  creado       CLAUDE.md
  creado       memory/.gitkeep
  creado       .mcp.json (servidor cad, adaptador freecad)
  creado       .claude/settings.local.json (hook de run_native, 1 CLAUDE.md excluidos)

Siguiente paso: el plugin ya está registrado en Claude Code. Abre esa carpeta:
  - en la app de escritorio de Claude: pestaña Code, y elige la carpeta; o
  - en una terminal:
      cd /home/you/design
      claude
```

If the plugin is not registered, the next step suggests `claude --plugin-dir <plugin path>` and reminds you of `cotaforge plugin register`.

### What each generated file is

| File | Purpose |
|---|---|
| `CLAUDE.md` | The agent's behavior. You can edit it. If you never edited it, CotaForge updates it with new versions and keeps the previous one as `CLAUDE.md.<version>-<fingerprint>.bak`. If you edited it, CotaForge leaves the new template in `CLAUDE.md.new`; the agent reminds you at the start of each session while that file exists and offers to merge. Only `--force` replaces it. |
| `.mcp.json` | Declares the `cad` server: the Python interpreter and the variables `COTAFORGE_ADAPTER`, `COTAFORGE_WORKSPACE`, `COTAFORGE_FREECAD_ROOT` and, if requested, `COTAFORGE_VISIBLE`. |
| `.claude/settings.local.json` | Allows the server's tools (`mcp__cad`), approves the `cad` server, excludes inherited `CLAUDE.md` files and registers the `run_native` hook (section 11). |
| `memory/` | Your project memory: one `.md` file per entry (section 10). |
| `assumptions-and-decisions.md` | Log of assumptions, decisions and calculations, each with its source. |
| `calculations/` | Python scripts that produce the design's numbers. |

Your `.FCStd` files (with their `.cotaforge.json`) and the logs appear as you work.

Folder and file names are English. A project created before 0.10 keeps its Spanish names (`memoria/`, `planos/`, `calculos/`, `supuestos-y-decisiones.md`, `friccion.md`, `<pieza>-intencion.md`). CotaForge reads them where they are and never renames them. The content you write or read (log, memory entries, sheets) stays in the project's language.

**Inherited `CLAUDE.md` files are excluded.** `init` finds `CLAUDE.md` files in the folders above your space and in `~/.claude` and adds them to `claudeMdExcludes`, so your personal configuration does not affect the design. It checks that they exist; it does not read them. This is why your global instructions do not apply in a design space.

### `cotaforge init` options

| Option | Effect |
|---|---|
| `--adapter freecad` (default) | Uses FreeCAD. |
| `--adapter fake` | A simulator for the project's tests. It produces no real geometry. |
| `--freecad-root PATH` | FreeCAD root folder, written in `.mcp.json`. |
| `--visible` / `--no-visible` | Opens FreeCAD **with its window** (`COTAFORGE_VISIBLE=1`) so you watch the design grow. `--no-visible` turns it off. |
| `--force` | Replaces `CLAUDE.md` even if you edited it. |
| `--plugin-dir PATH` | Only changes the "next step" message to `claude --plugin-dir PATH`. |
| `--language es\|en` | Your language in this project: `init` messages, drawing sheets, the drafter's questions, decision notes. Stored as `language` in `brief.yaml`. Without the option, an existing project keeps its language, a new one takes the language chosen at install, and an older one that declared none stays in Spanish. Change it later with `memory(action=brief, language=...)` or by asking the agent. |

If FreeCAD is not found, `init` still creates everything and warns. Pass `--freecad-root` and run it again.

**It is safe to repeat.** `init` never touches memory, keeps an edited `CLAUDE.md` (you see `conservado`) and merges JSON files without deleting keys that are not its own. Use it to switch to `--visible`, fix the FreeCAD path or refresh the interpreter.

### Opening Claude Code with the plugin

With the plugin registered (the Windows installer does it; from a clone, `cotaforge plugin register`):

- **Desktop app:** **Code** tab, choose the design space folder.
- **Terminal:** `cd ~/design`, then `claude`.

Type `/cotaforge:` to see the commands. Without registering, load the plugin for one session from a clone: `claude --plugin-dir /path/to/clone/plugin`.

### What Claude Code may ask the first time

- **Trusting the folder.** The usual Claude Code question for a new folder.
- **Approving the `cad` server.** `init` already approved it. If it asks anyway, approve it. `/mcp` shows whether `cad` is connected.
- **Tool permissions.** `mcp__cad` is allowed, so it should ask nothing. The exception is `run_native` with risky code (section 11).
- **Writing files.** Commands such as `/cotaforge:friction` write to your folder, so Claude Code may ask for write permission.

---

## 6. Your first design

1. Ask with `/cotaforge:plan`. For example: "A 60 x 40 x 10 mm plate named `plate`, with an Ø8 through hole centered on the top face and a radius 5 fillet on the origin corner. Tell me the volume after each step."
2. Answer the agent's questions. It asks only what it cannot decide.
3. Read the plan: `inspect` with `action="tree"`, or ask "show me the operation tree".
4. Watch it build. With the FreeCAD window visible (`--visible`) you see each operation arrive. Each step reports the measured volume and bounding box.
5. Check: ask for `check` against what you declared, or run `/cotaforge:review`.
6. Make the drawing. The agent builds the sheet, someone other than the agent reviews it, and then you export the PDF (section 7, "Drawings").
7. Save with `doc` (`action="save"`). The `.FCStd` stays in your design space.

If a step fails, the server says so and the model stays as it was. The agent reads the reason and retries. To undo a step, ask: "undo the fillet".

The website has the full walk-through: <https://jomruizgo.github.io/cotaforge/first-part/>.

---

## 7. How the agent works

### The tools

The agent talks to the CAD software only through these tools of the `cad` server (permissions show them with the `mcp__cad__` prefix). Each tool has several actions; `cad_guide` and the tool descriptions give the agent the exact parameters.

| Tool | What it is for | Main actions |
|---|---|---|
| `doc` | Document lifecycle (several can be open) | `new`, `open`, `import` (STEP or IGES), `save`, `status`, `close`, `switch`, `set_material`, `set_route`, `set_purpose`, `convention`, `reload` |
| `op` | One modeling operation, with measured facts | chosen with `name`; `edit` changes a step or feature; `delete` removes a feature |
| `undo` | Undo | `undo()`, `undo(to="<step>")`, `undo(to="start")` |
| `inspect` | Read measured facts, change nothing | `tree`, `faces`, `edges`, `face`, `edge`, `properties`, `measure`, `bom`, `validity`, `interference`, `sketch_status`, `sweep`, `holes` |
| `view` | Show you things and read your selection | `focus`, `show`; with a window: `selection`, `capture`, `set_view`, `style`, `section` |
| `drawing` | Production drawings | `create`, `add`, `remove`, `remove_sheet`, `update`, `list`, `review`, `capture`, and the review actions (see "Drawings") |
| `channel` | Question channel between the drafter session and the design session | `ask`, `reply`, `dispute`, `decide`, `wait`, `done`, `list`, `open_drawing`, `reopen`, `close` |
| `export` | Output to other formats | `step`, `mesh`, `macro`, `pdf`, `dxf` |
| `check` | Compare the measured part with what you declared | no arguments |
| `memory` | The engineer's memory | `index`, `get`, `search`, `save`, `deactivate`, `approve`, `answer`, `review`, `brief` |
| `catalog` | Commercial parts, your catalog and the technical library | `search`, `get`, `add`, `file` |
| `run_native` | Escape hatch: FreeCAD code as an undoable step | section 11 |
| `cad_guide` | On-demand knowledge of the CAD software | topics such as `profile`, `ops`, `op:<name>`, `terms`, `traps`, `native`, `recipe`, `drawing`, `assembly`, `memory`, `pointing` |

### One operation per call

One `op` call runs **one** operation and returns volume, bounding box and validity. Operations with FreeCAD:

- **Sketches and references:** `sketch`, `ref_plane`, `ref_axis`.
- **Features:** `extrude`, `cut`, `revolve`, `sweep`, `hole`, `linear_pattern`, `circular_pattern`, `chamfer`, `fillet`, `shell`, `draft`, `mirror`.
- **Bodies:** `box`, `cylinder`, `boolean_union`, `boolean_subtract`, `boolean_intersect`, `translate`, `rotate`, `copy_body`, `mirror_body`, `split`.
- **Assembly:** `insert_component`, `place_component`, `fix_component`, `add_joint`, `set_joint`, `mate_concentric`, `mate_coincident`, `mate_distance`, `check_interference`.
- **Sheet metal** (needs an add-on, see [Sheet metal](#sheet-metal-optional)): `base_flange`, `edge_flange`, `unfold`.

Before using an operation the agent reads `cad_guide` with `topic="op:<name>"`. `topic="terms"` translates commercial CAD vocabulary ("extruded boss", "hole wizard", "concentric mate") into these operations.

Everything is in millimeters, mm³ and degrees. Step names are free (lowercase letters, digits, `_`); without one the agent uses `s1`, `s2`... Each step carries an `intent` line with the why.

### Holes

`hole` follows FreeCAD's hole wizard (`PartDesign::Hole`) with the same names and defaults.

| Field | What it does |
|---|---|
| `standard`, `size` | `simple` (with `diameter_mm`), `iso_clearance` (ISO 273 clearance, M8 gives Ø9) or `iso_tapped` (tap drill, M6 gives Ø5; the thread is cosmetic). `size` accepts coarse metric threads up to M68 and fine ISO 261 threads (`M45x1.5`). The tap drill is calculated (major diameter minus pitch); M8 and M12 use their standard drills. The ISO 273 clearance is tabulated up to M36; beyond that, give `diameter_mm`. The tables were transcribed without the text of the standard and checked size by size against FreeCAD's own. |
| `end_condition` | `blind` (`depth_mm` from the entry face, including the counterbore), `through_all` or `up_to_face`. |
| `counterbore` | `{diameter_mm, depth_mm}`. An M8 socket-head bolt is one call: `iso_clearance`, `size="M8"`, `counterbore={diameter_mm: 14, depth_mm: 9}`. |
| `countersink` | `{diameter_mm, angle_deg}` (90 by default). Not combined with `counterbore`. |
| `drill_point` | `angled` (default, 118° cone added after `depth_mm`) or `flat`. A through hole ignores it. Designs saved before this default keep a flat bottom. |
| `up_to_face` | The end face must be planar, normal to the axis and ahead of the entry. FreeCAD has no such condition, so the server measures the distance every time the step runs and drills blind to that depth. The response says so. If the face moves, edit or repeat the step. |
| `fit` | `"H7"` and similar: the hole's fit, used for its color and for drawings. |

Errors appear before the part is touched and say which number does not add up. Against a cylindrical face only simple holes work.

**How it is verified.** Before a `hole`, `cut` or fused `extrude`, the server asks FreeCAD for one boolean operation: the tool solid against the part as it is. The expected volume change is the real intersection, not "area x depth". A hole that breaks into an existing cavity passes. A tool in the air, the wrong side, or a volume different from the boolean's still fails. The response says what was compared. Without FreeCAD (the test adapter) a closed formula is used.

**Hole colors.** A harness convention, nothing to configure: a threaded hole is light blue, a precision hole (fit IT8 or finer, such as H7) is soft red, and any other is green. Holes made by `hole` stay painted. A color you change by hand is respected. In drawings only the isometric view is colored. To change the values, write `convenciones.yaml` in the data folder (`COTAFORGE_HOME`, default `~/.cotaforge`) for all projects, or in the design space for one project (the project file wins). You can also ask the agent: it uses `doc` with `action="convention"`.

```yaml
agujeros:
  activa: true                  # false: no hole is painted
  colores:
    preciso: [255, 128, 128]    # or "#ff8080"
    liso: [0, 192, 0]
    roscado: [128, 128, 255]
  grado_it_preciso: 8           # a fit of this IT grade or finer is precision
  isometrico_en_color: true     # false: the isometric stays in line
```

`inspect` with `holes` lists cylindrical holes grouped by diameter, with axis, depth and through or blind. A counterbore appears as two holes on one axis.

### Concept sketch or detail

`doc` with `action="set_purpose"`, `purpose="sketch"` or `"detail"` (default). A `sketch` document is a concept model: `box`, `cylinder` and booleans are fine to probe volumes. A `detail` document is built with features (sketch + `extrude`/`cut`, `hole`, `fillet`) that stay editable. In a detail document, subtracting a cylinder head-on into a planar face gets a warning with a ready-to-use `hole` call. The purpose is stored in `<name>.cotaforge.json`.

### Undo and edit

`undo` undoes the last step; `undo(to="s2")` undoes everything after `s2`; `undo(to="start")` undoes everything. Features that were already in a file when it was opened cannot be undone (section 8).

To change a value of an earlier step, do not undo: `op(edit="s3", params={"diameter_mm": 10})` changes only that value and regenerates what depends on it. If a later step no longer fits, the response says which and the model stays as it was. Sketch dimensions: `params={"dimensions": {"largo": 80}}`.

### Features of an opened file

A file made by hand in FreeCAD, by `run_native` or by another session brings its feature history. The agent sees the features with their **FreeCAD names** and can change them without `run_native`:

- `inspect` with `action="tree"` lists each body's features in chain order, with their main parameters, the tip and which plan step made each. With `id="Chamfer"` it gives one feature in detail.
- `op(edit="Fillet", params={"Radius": 4})` changes properties by FreeCAD name. The value is validated and the result is recomputed and checked. If the result is invalid, nothing is applied. `params={"edges": [...]}` reassigns the edges of a fillet or chamfer.
- `op(delete="Chamfer")` deletes a feature as FreeCAD does. If other objects need it, it is rejected naming them; `params={"cascade": true}` deletes them too. Deleting a body's last solid feature is rejected (use `undo`).
- A feature made by a plan step (`extrude`, `fillet`...) is edited with that step: `op(edit="s3", ...)`.

Each change is one step and `undo` reverts it. `view` with `action="focus"` shows each body's last feature and hides sketches and intermediate features.

### Selecting faces and edges

A **face** is a surface of the part. The agent names it by description: "the +Z normal face created by step `s2`". A reference must match exactly one face; if it matches several, the error lists the candidates and the agent refines.

- Positions on a face are measured **from the face's center**, not the model origin. "Centered on the top face" is position `(0, 0)`. A hole also accepts `at=[x, y, z]` (projected onto the face), and a sketch on a face accepts `frame="world"`.
- On the base planes the sketch coordinates are world ones: XY is `u=X, v=Y`; YZ is `u=Y, v=Z`; XZ is `u=X, v=-Z`.
- Internal face ids change as operations apply, so the agent asks for faces by the step that created them.

`chamfer` and `fillet` also accept an **edge set**: you describe what the edges are and the server resolves them on the current part.

```json
{"kind": "edges", "from_op": "plate", "where": {"parallel_to": "z", "convex": true}}
```

`where` criteria: `parallel_to`, `length_mm`, `convex`, `planar`, `curve` (`line`, `circle`, `arc`), `on_face`, `exclude_holes`, `except`. All given criteria must hold. The step reports how many edges matched and lists them. The agent can preview a set with `inspect` (`action="edges"`, `op=..., where={...}`). If FreeCAD cannot handle some edge, the step fails and names the edges; with `skip_failing=true` it applies the ones that fit and reports the rest.

### STEP and IGES import

`doc` with `action="import"`, `path` (`.step`, `.stp`, `.iges`, `.igs`) and `name` brings a customer's part into a new part document.

- The geometry stays where the file puts it. It is not recentered.
- FreeCAD converts to millimeters. The result states the unit the file declares.
- It creates one body per solid, saved as `<name>.FCStd` in the design space. If that file exists, the import is rejected unless `overwrite=true`.
- The result reports solids, volume, bounding box and whether the geometry is valid and closed. A file with only surfaces (common in IGES) is warned about, since volume is not meaningful. Loose faces go into one surface body; `sew=true` tries to sew them into solids.
- Imported bodies have no history: `op` with `edit` cannot change them. You can still build on them (sketch on a face, `cut`, `hole`, `fillet`) and save.
- Large files take minutes and the server sends progress notices. An import is cut off after `COTAFORGE_IMPORT_STALL_S` seconds (120 by default) without FreeCAD using CPU, or after `COTAFORGE_IMPORT_MAX_S` (3600 by default).
- Imported bodies get FreeCAD's cheap validity check. The deep check is `inspect` with `action="validity"`.
- Parasolid (`.x_t`, `.x_b`) cannot be read. Ask for STEP.

Long `inspect` results that do not fit are cut and the full text is saved in `reports/`; the result names the file.

### Assemblies

Each part is modeled in its own document with its own axes, then placed in an assembly document.

1. Part: `doc` `new`, operations, `save`. Parts stay open; `doc` `status` lists open documents and `doc` `switch` chooses which one `op`, `undo` and `inspect` act on.
2. Assembly: `doc` `new` with `kind="assembly"`.
3. `op` `insert_component`: `part` is the name of a saved part (or the file of a saved part that is not open) and `placement` is its origin and rotation. For two identical screws, insert the same part twice.
4. `place_component` moves an instance. The pose is absolute.
5. `inspect` `interference` reports each pair of instances as overlapping, touching or clear (with the minimum distance).

**Joints.** `add_joint` creates a `fixed`, `revolute` or `slider` joint between instances (`b` moves, `a` is what it hangs from; without `a`, the ground). The joint is created at the current position, which is value 0, and `min` and `max` must include 0. `set_joint` takes it to an absolute value. An instance with a joint is moved with `set_joint`, not by hand. `inspect` with `sweep`, `id` and `range=[from, to, step]` steps the joint through positions and reports the first overlap. It checks only those positions, so the agent should say "checked every 1°", not "no collision".

Changes to a part reach the assembly when you `switch` back to it. An unsaved part is not what the assembly sees: it links to the saved file. The assembly file names its parts by relative path, so keep them together when you copy or move it. A part used by an open assembly cannot be closed until the assembly is.

**Windows, with the window visible.** A FreeCAD link requires the linked document to be loaded, not visible. A part that the agent made and saved loses its window when inserted (it stays open). To edit it, `doc` `switch` to the part, edit, `save` and `switch` back.

### The visible window (`--visible`)

By default FreeCAD runs without a window. With `--visible` (or `COTAFORGE_VISIBLE=1`) it opens its window and you watch the model grow. It needs a display (`DISPLAY` or `WAYLAND_DISPLAY` on Linux). Visible FreeCAD uses its own preferences folder, not yours. This enables:

- `view` `selection`: the agent reads what you selected in 3D or on a drawing sheet.
- `view` `show`: the agent points out faces, edges, drawing elements or views.
- `view` `set_view`: `iso`, `front`, `top`, `right`, `rear`, `left`, `bottom`, `dimetric`, `trimetric`.
- `view` `style`: transparency, color, show or hide bodies.
- `view` `section`: a cutting plane by axis and position, to see inside.
- `view` `capture`: a PNG of the 3D view.

`style` and `section` only change what you see, not the model.

**What you change by hand.** If you edit the model in FreeCAD (a property, a sketch dimension, a feature you add or delete, your own undo), the **next response from any tool** starts with a short notice, for example:

```text
hand edits in the CAD since your last call (the engineer changed the model; re-read what you rely on):
- Pad (step base): Length: 16.0 -> 18.0
```

It is delivered once and does not wake the agent. Camera, visibility, color and selection changes are never notified. Selection is read only on request. Hand changes on a drawing sheet are not notified either. Without a window there are no hand edits.

Without a window, `view` offers only `show` and `focus`.

**Sketches.** A sketch that an operation already used is hidden when the operation finishes. Hiding is display-only and does not leave the document unsaved.

**Pointing at the drawing.** With the window visible you can select in the sheet window a dimension, callout, view, or a line or circle of a view (Ctrl adds several) and ask about it. The agent receives its id and type, the text it prints, what it measures and the model faces and holes it refers to. A line or circle of a view maps to a model edge and its faces. The other way works too: select a face in 3D and ask "which dimensions refer to this face?". The agent can point at you with `view` `show` and `ids` (drawing elements `d3`, views `view:top`, faces or edges), with `on="sheet"`, `"3d"` or `"both"`; `clear=true` removes the marks. It changes nothing in the document.

### Export

`export` with `step` writes a STEP file, `mesh` an STL and `macro` a FreeCAD script that rebuilds the model. Relative paths are relative to the design space. **If `check` has violations, export is blocked** (section 9). Saving and editing stay allowed. `export` never overwrites: it answers `file_exists`; add `overwrite=true` to replace.

- `pdf` is always the drawing: one sheet, one file. With several sheets, name one with `sheet=`.
- `dxf` with `face` or `flat` is a cutting outline (below). Without them it is the drawing sheet.
- The PDF and DXF of a sheet are written only when the drawing is finished (see "Finished drawing"). The cutting DXFs do not go through that gate.

**DXF for laser or plasma cutting.** `export` `dxf` with `face` (a flat face, by id such as `face:3` or by the same reference operations use) writes the outer contour and all voids at real scale in mm, projected on the face's plane with the lower left corner at (0, 0). Arcs stay arcs. A curved face is rejected. The DXF carries no thickness or material: confirm the unit and thickness with your shop. With `flat="<unfold step>"` instead of `face`, it writes the flat pattern of a sheet-metal part, without bend lines.

### Drawings

`drawing` builds the part's shop drawings. Each drawing is a **sheet**; a document can have several, each exported to its own file (no multi-page PDF). The workflow: `create` the sheet, `add` what is missing, `review`, export.

**Built live.** With the window visible, the TechDraw sheet opens on `create` and each element appears as the agent adds it, so you can interrupt or suggest. The agent works in small steps. Without a window the drawing is built the same; you see it with `capture` or on export.

**`create`.** Parameters: `template` (`A4`, `A3`, `A2`, `A1`, `A0`, `Letter`; landscape), `angle` (`first` default, or `third`), `title`, `author`, `date`, `material`, `scale`, `drawing_number`, `revision`, `general_tolerances` (default `ISO 2768-mK`), `company`, `title_block` (more fields by name), `views`, `isometric`, `projection` and `fresh`. The title block follows ISO 7200; its language is Spanish unless you pass `language="en"`. The scale is chosen automatically unless you give one (`scale="1:2"`).

- Without `views`, a part of revolution gets the view along its axis and an A-A section through the axis; any other part gets four views (front, top, right, isometric). `remove_view` and `add_view` with `views=["right"]` remove or restore a view.
- `create` on an existing sheet rebuilds it and keeps what is still valid: elements, review answers, the chosen reviewer, title block, paper, angle, language and scale. It lists the elements that no longer resolve instead of deleting them. `fresh=true` builds from scratch.
- For another sheet of the same document use a new `sheet` name. `body` draws one body per sheet.
- **`projection`** is the engineer's choice; the agent never picks it. `techdraw` (default) makes native TechDraw views, which can take hours per view on an assembly of hundreds of bodies. `hlr` projects the standard views with `TechDraw.projectEx` and takes seconds or minutes. The drawing is the same, but the window's views are drawn symbols, not editable TechDraw views. On a slow large assembly the agent may offer `hlr` and wait for your answer.

**What is native in TechDraw.** Native objects, which you can edit in FreeCAD: the views (with `projection="techdraw"`), `hole_dim`, `dim` (FreeCAD measures the value), `datum_dims` in `ordinate` mode, `note`, the overall dimensions and the title block. Drawn symbols: `datum`, `gdt`, `finish`, centre marks and lines, thread arcs, `section`, `detail`, `aux_view`, the exploded view, the parts list and the hole table. The reason is that TechDraw 1.1.3 has no equivalent object, or it does not draw to ISO style. If a native dimension does not match the model, the element is drawn as a symbol and `review` reports it. A text or position you edit by hand is respected until the element changes. PDF and DXF are what you deliver: with the window, TechDraw writes the PDF; without it, CotaForge's own compositor does, and the `export` response says which.

**Elements (`add`).** `drawing` `add` with `items=[...]`; each has a `type` and optionally an `id`. The response says per element: `ok`, `warning`, `error` (stored, cannot be drawn now) or `refused` (not stored). **The agent never writes dimension numbers: they are measured on the model** every time the sheet is drawn.

| Element | What it places |
|---|---|
| `hole_dim` | Hole callout with leader, in the view where the circle shows (`4× Ø8 pasante`, `M6 ↧ 12`). Identical holes share one callout. Choose holes with `at`, `face` or `all=true`. `dowel=true` marks locating holes. `text` gives your wording of the callout; it is checked against the model and `review` warns if it contradicts it. `thread` declares the thread of a hole not made by `hole`. `view="A-A"` puts it on a section. |
| `dim` | `kind="distance"` between two references (a point or hole axis, a face, an edge), `radius`, `angle`, `chamfer`, `diameter`. |
| `datum_dims` | Hole positions from two reference edges: `mode="ordinate"` or `mode="table"`. Each sheet with holes already carries its own (`loc`, `auto=true`). |
| `line` | An ISO 128 line (`chain`, `dashed`, `continuous`): an `axis` of a cylinder or a line `from` and `to`. |
| `note` | A numbered note in the `NOTAS:` block. |
| `revision` | A row of the revision table. |
| `overall` | The three overall dimensions (made by `create`). |
| `view_lines` | Hidden lines and axes in the standard views. Without it they are not shown. Add it on every part with holes. |
| `section` | A full section with cutting line, letters and 45° hatching. Give `holes="all"` or `at` and `normal`. Half sections are not supported. |
| `detail` | An enlarged circle of a view, labelled `B (5:1)`. |
| `aux_view` | The part seen head-on to an inclined face. |
| `datum` | An ISO 5459 datum triangle with a letter, on a `face` or on the axis of a `hole_dim` or diameter `dim` (`on`). |
| `gdt` | An ISO 1101 geometric tolerance frame (`char`, `value`, `datums`...). Citing a datum that does not exist on the sheet is an error. A geometric tolerance written as the text of a note is flagged (`gdt_as_note`), and so is a datum no frame cites (`datum_unused`). A position frame hangs under the size callout; the dimensions that locate its feature are boxed (theoretically exact; the ones you add take `exact=true`) and its first datum must fix the axis (the face the hole is perpendicular to). |
| `finish` | Surface finish (ISO 21920-1): `ra`, `machining`, or `general=true` for the "all other surfaces" symbol. |

Elements with a leader accept `offset_mm` and `side`. Without them the callout looks for free space. With them it goes exactly where asked, with a `warning` if that place is occupied. `at_mm` is a position on the sheet from its lower left corner.

`remove` with `items=["h2"]` removes only those elements (ids required). `remove_sheet` removes a sheet. `list` shows each sheet and what each element does against the part. If you changed the part, `update` rearranges the views and keeps all elements. An element that points to a face that no longer exists is reported as `error`; it is never drawn with the old number.

**Fits and tolerances.** `hole_dim` and `dim` accept `fit` (`"H7"`, `"g6"`, `"H7/g6"`; ISO 286, grades IT5 to IT11, up to 500 mm), `tol` (`0.1` or `[upper, lower]`) and `show` (`symbol`, `deviations`, `both`, `limits`). The nominal size is the model's. A dimension without its own tolerance reports the ISO 2768 general tolerance of the sheet.

A fit prints its class and, for one class, its limits (`Ø20 H7 (+0.021/0)`). The sheet carries no note asking the shop to verify them.

> **Not verified.** The ISO 286 and ISO 2768 tables were transcribed by hand from handbooks and checked against the usual fits, not against the text of the standard.

Threads (ISO 6410) are drawn automatically for holes made by `hole` as a three-quarter arc at the major diameter.

**Review and write.** `drawing` `capture` returns the sheet as an image (with `region=[x0, y0, x1, y1]` in sheet mm to zoom). With the window the image comes from the same sheet scene as the PDF, including what a `run_native` put on the sheet. Without a window, native objects that cannot be painted are listed as `NOT DRAWN`. `review` does not read native objects; it warns with `native_unread`, and checking that part means looking at `capture`. `export` `pdf` validates the PDF it just wrote and does not save it if it is malformed.

The DXF is R12, with layers by role (`CONTORNO`, `OCULTAS`, `COTAS`, `TEXTO`, `ROTULO`, `EJES`, `SOMBREADO`, `ROSCAS`), and the title block inside. ISO 128 line weights are applied in the PDF; in the DXF they are carried by layer color.

**Assembly drawings.** `create` on an assembly draws all components in their pose, without hidden lines, and leaves room for a parts list. These elements exist only on assemblies: `parts_list` (ISO 7573; one row per distinct part, quantities computed), `balloons` (ISO 6433; one per position, on a visible face of an instance in the chosen `view`) and `exploded` (components separated, with `mode="radial"` or `"axis"`, `moves` to set a component's displacement, and trajectory `trails`). The exploded view is a transformation of the drawing; the assembly does not move. Welding is out of scope.

**`drawing` `review`.** It draws nothing and changes nothing. It lists, without a language model, what a reviewer would flag, each finding with an `id` and the element involved: holes without a callout or position, features without a dimension, over-dimensioning, undefined datums, references to faces that no longer exist, layout collisions and (on assemblies) parts-list and balloon problems. `warning` is a probable error and `info` something to confirm. Use it while you build the sheet; it does not replace looking at `capture`.

### Finished drawing: who reviews and what blocks export

`export` `pdf` (or `dxf` without `face` or `flat`) is **rejected until someone other than the agent that drew the sheet has reviewed it and nothing is left unresolved**. The response says which case the sheet is in and what is missing. The server does not decide that a drawing is good. It forces the agent to have someone else look at the sheet and to answer every point.

There is no draft PDF. The draft is the TechDraw sheet inside the `.FCStd`; you see it live and with `capture`.

**Who reviews: `drawing_review`.** Stored in `brief.yaml` next to the sector and processes. Change it with `memory(action="brief", drawing_review="<mode>", text="<your words>")` or by editing the file. Moving off the default is your decision: the agent must quote your words in `text`.

| Mode | Who reviews | What opens the export |
|---|---|---|
| `engineer_or_fabricator` (default) | You choose to review yourself or have the `fabricator` agent do it. If you do not answer within `drawing_review_wait_min` minutes (15 by default), the fabricator counts. | The report of whoever counts, with all its points answered. |
| `fabricator` | Only the `fabricator` agent. | Its report, all points answered. |
| `engineer` | Only you, with no time limit. | Your report, all points answered. |
| `own` | The drawing agent asks itself a checklist. | The checklist, closed. |
| `none` | Nobody. | Nothing. Not even the automatic findings are requested. |

The old Spanish values `ingeniero_o_fabricante`, `fabricante`, `ingeniero`, `propio` and `ninguno` are still accepted as input. Set the wait together with the mode (`drawing_review="engineer_or_fabricator:30"`) or alone (`drawing_review="30"`).

**The `fabricator` agent** plays a machinist, welder or assembler who receives only the drawing: the sheet image and its printed texts, never the 3D model. It answers whether it can manufacture and assemble the part, what is missing, what is ambiguous and what is costly as drawn. It uses your sector's drawing memory entries as a checklist. It records its report with `drawing(action="review_report", by="fabricator", items=[...])`. Each point has a `kind` (`missing`, `ambiguous`, `costly`, `other`), `where`, `text` and `fix`, and gets an id such as `fab1.1`. It cannot modify the sheet.

**The default flow.**

1. The agent calls `drawing(action="request_review", sheet)`, which notes the time on the sheet. It asks you one question: will you review the sheet or should the fabricator? It starts a background timer so Claude Code wakes it at the deadline, and carries on meanwhile.
2. If you say "me", the agent records it (`review_choice`, `by="engineer"`) and waits for your report with no time limit. You dictate observations in chat (or "no observations"). What you point at on the sheet it reads with `view` `selection`. It records them with `review_report`, `by="engineer"`.
3. If you say "fabricator", or do not answer in time, the agent launches the `fabricator` at that moment.
4. Before export, every point of the latest report that counts is answered. The agent fixes the sheet and answers with `drawing(action="answer", id="fab1.1", text="fixed: what changed")`, or answers `does not apply: why`. If you reviewed, even after the fabricator, your latest report counts.
5. The choice of reviewer belongs to the sheet: a later change does not ask again. A report stays in force after the fixes it asked for. The drawing keeps a change counter and shows "reviewed at change 3, the sheet is at 5". A new review happens only if you ask or the agent decides the sheet changed beyond the report.

**The server cannot prove who wrote a report.** Any agent can call `review_report`. It guarantees the order (the chosen reviewer's report, all points answered) and that the answers stay on the sheet, where you read them with `review`.

**Automatic findings.** In every mode except `none`, CAD automatic findings still hold back the export. They are computed on the real geometry and on what the sheet draws, never guessed. They cover hole positions and their references, fit holes without tolerance, undefined datums, features without dimensions, missing centre marks and axes, legibility, and the standard notes (material, edges, heat treatment, coating, roughness, marking). Each note is present or declared "does not apply". `drawing` `review` lists them with their ids.

**Only in `own` mode** the agent also answers judgment questions and the memory entries that the drawing export triggers, which it must have read in this session.

**How a point is closed.** Fix it (add the dimension, note or section) or answer it once: `drawing(action="answer", sheet?, id, text)` with `text` = `applies: <how, on this part>`, `does not apply: <why>` or `fixed: <what changed>`, at least three words after the colon. Example: `id="notes_heat_treatment"`, `text="does not apply: aluminum 6061-T6 is delivered treated"`. A finding neither fixed nor answered stops the export. Window warnings (`window_differs`, `native_fallback`) never stop it.

Answers live in the sheet, inside the `.FCStd`, and survive save and reopen. Read them: a "does not apply" without a real reason is a false statement to you. **An answer is not printed on the drawing.** What the shop must read goes in a `note`.

**"Approved by" in the title block is yours.** Only you fill it, with `drawing(action="approve", sheet="Plano", by="Ana", reason="Ana: approve it")`. The agent calls it only when you order it, quoting your words. Any later change to the sheet empties it again. Approving does not skip the review.

**Not verified in a real Claude Code session:** that the background timer wakes the agent and that the agent launches the `fabricator` by itself. The clock, the gates and the reports are tested with the real server and FreeCAD.

The agent's guide for this is `cad_guide` with `topic="drawing:done"`.

#### A separate drafter

Drawings can be made by another agent, the `drafter`, in its own Claude Code session so you can watch and correct it. Its context is only the drawing rules and the sheet. It does not know the design intent, so it asks what is not in the intent file or the model.

1. **Handover.** The designer saves the part, writes `drawings/<part>-intent.md` and closes the part. A file is open in only one session at a time, since the one that saves last overwrites the other. It calls `channel` `open_drawing`, which opens a terminal in the design folder running `claude --agent cotaforge:drafter`. If it does not open, the designer gives you the command to type yourself.
2. **Questions.** The drafter asks the designer with `channel` `ask` and waits (`wait`). The designer answers from what it knows and asks you only what it does not know. You can answer in the drawing session (`by=engineer`). Everything is kept in `drawings/<part>-questions.jsonl` and a readable copy `drawings/<part>-questions.md`.
3. **An answer is not a decision.** The designer checks what you told the drafter against the intent, calculations, standards and model. If it matches, it notes it in `assumptions-and-decisions.md`. If not, it tells you why and the point stays in `dispute`. The final decision is yours; it is recorded with `decide`, with the objection beside it.
4. **Model changes.** The drafter never changes the part. It asks through the channel, saves and closes; the designer changes and closes; the drafter reopens and runs `update`.
5. **Review.** The drafter carries the review gate, then exports and tells the designer with `done`.
6. **Closing the window.** When the drafter records `done` (sheet saved, no fabricator point open, no question open), the harness closes that window by its own pid, never by name. If someone is mid-turn or it cannot be known, it tells you and does not close it. `close` with `confirmed=true` closes it at your request.
7. **If you close the window by mistake.** The drafter saves after every change. The designer finds out and offers `reopen`: the same conversation with `claude --resume <id>` (the id is in `drawings/<part>-window.json`), or a new session that reads the channel and the sheet.

Channel roles are `drafter` and `designer`; the human is `engineer`.

**Not verified in a real session:** two sessions on the same folder, the window `open_drawing` opens and its closing by pid, `claude --resume` with `--agent`, reading the transcript to know whether a turn is in progress, and the CAD software's behavior when one session saves and closes a part and the other opens it.

#### Minimum drawing quality

Every sheet gets this floor without you asking for it. Paper, projection and title block remain your choice.

- **Every hole located.** A sheet with holes gets a `loc` element. It never counts from the corner of the bounding box. In each direction it counts from your declared datums (baseline dimensions), otherwise from the part's plane of symmetry, otherwise it draws nothing and asks you from which face or edge positions are counted.
- **Compact callouts.** Identical holes share one callout. Leaders go to the cleanest side and do not cross each other.
- **Dowel holes.** `dowel=true` adds ◎ to the callout and a ring on each hole. The function of a hole is yours to say; `review` warns about a likely dowel hole that is unmarked.
- **Numbered notes.** The `NOTAS` block numbers, in order: thickness (when the sheet has one standard view), and the agent's notes.
- **Title block.** A TechDraw template stored in the document. `create` or `update` with `title_block={"document_type": "...", ...}` sets fields by name. There is no logo yet.
- **A single view.** A plate whose holes go through the thickness can be drawn in one view with a thickness note (`views=["top"]`).

Not supported: broken views, partial sections and bend lines in sheet metal.

### Sheet metal (optional)

CotaForge models bent sheet metal with `base_flange` (a sketched outline with holes inside becomes a sheet), `edge_flange` (bends a wall from a straight edge) and `unfold` (flat pattern). They need the **SheetMetal** add-on, which does not come with FreeCAD (<https://github.com/shaise/FreeCAD_SheetMetal>).

**Install it:** in FreeCAD, *Tools > Addon manager*, search `SheetMetal`, *Install*, then restart FreeCAD and Claude Code. The server offers the three operations only when it detects the add-on. If it is missing or does not load, `cad_guide` and the error say how to fix it.

The server reads the user `Mod` folders where the Addon Manager installs (on Windows `%APPDATA%\FreeCAD\v1-1\Mod` and `%APPDATA%\FreeCAD\Mod`), because the visible window uses a private profile. If yours is elsewhere, set `COTAFORGE_FREECAD_MOD_DIRS` to the `Mod` folders, separated by `;` (Windows) or `:` (Linux/macOS).

**Use:**

1. `base_flange`: a sketch with the outline and, inside it, holes and cut-outs, plus the thickness. A sheet-metal part does not admit `cut`, `hole` or `fillet`; holes go in this sketch.
2. `edge_flange`: an edge between a large face and its thickness face. Parameters: `length_mm`, `angle_deg` (90 by default), `bend_radius_mm` (inside radius, **no default**: it is your tooling's), `position` and `length_from`.
3. `unfold`: a large flat face (fixed) and **`k_factor`, mandatory and with no default**. It is the ANSI K factor (0 to 1, usually 0.3 to 0.5). It depends on material, thickness, radius and tooling: **ask the shop**. The agent must state the value it assumes in `intent`. A wrong K gives a wrong flat pattern.

`unfold` reports the flat pattern size, thickness and blank area. The volume and bounding box that keep appearing describe the bent part. CotaForge also computes the flange volumes and the flat pattern size by formula and compares them with FreeCAD's; if they disagree, the step is undone. This check is required only in the simple case (rectangular outline, one full-length flange per side, no hole inside a bend) and reported otherwise.

For cutting, `export` `dxf` with `flat="<unfold step id>"`. Bend lines are not included.

Limits: only flanges on straight edges; flanges on flanges are not tested; no hems or configurable reliefs. Verified with add-on 0.8.26 and FreeCAD 1.1.3 on Linux and Windows, without a window; with a window it is not verified.

### Mass and bill of materials

`inspect` `properties` reports mass = measured volume x density and names the density's source. Density comes from the `density_kg_m3` property of the declared `material` entry in memory, or from a short included table with its source cited (structural steel 7850 kg/m³, aluminum 6061 2700; stainless 304 and 316 are marked unverified). Without a known density it answers `mass: not available` with the reason; the agent does not guess one.

`inspect` `bom` lists one line per distinct body with quantity, volume, mass, material and route, and the total mass.

### Catalog of commercial parts

The agent consults `catalog` instead of remembering dimensions. It has two parts:

- **Included standard tables.** Structural tubes (NTC 4526:2021, Ternium Colombia catalog), equal-leg angles (ASTM A36), sheet gauges and ISO metric hardware (hex screws and bolts ISO 4014/4017, nuts ISO 4032, washers ISO 7089). Each result states its sizes and source; the sizes covered are in the results.
- **Your catalog**, in `~/.cotaforge/catalog/`: one readable JSON file per entry, each with its date. Role `compra` (default) is a supplier you buy from, with availability and price and unit. Role `medidas` is a table of dimensions from a catalog that is only a reference. It has the standard and source, and no supplier or price.

Ask in your own words: "mass of a 40x40x2 square tube". The agent uses `search` and `get`. To add something, tell it "save this quote" (name, supplier, date, price and unit) or hand it a table and say "save these dimensions as a reference". It adds only what you gave or confirmed and never invents a price. A new price is a new entry. To correct or remove an entry, edit or delete its file.

Each result states where the number comes from: `catálogo (NTC 4526:2021, ...)`, `catálogo del usuario (proveedor, fecha)`, `catálogo del usuario, solo medidas`, `calculado`, or `SIN VERIFICAR` (confirm with the supplier before use).

The catalog is small and is not the only source. If a part is not there, the agent searches the web and marks it "web (URL, date)" or as an assumption. The gauge of a sheet names a table: confirm thickness in mm with the supplier. The published mass of tubes can exceed the nominal section's by up to 13%; the tool shows both.

### Buy before you draw

The agent asks itself whether a part that transmits, guides, seals, fastens or supports in a standardized way (bearings, couplings, seals, keys, springs, gearmotors) is commercial, and looks in `catalog` and then on the web.

- The search is logged in `assumptions-and-decisions.md`, one line per part: what, where, what was found and the decision (buy, or manufacture and why). The reviewer checks that each body that looks commercial has its line.
- If you buy and have the STEP, give it to the agent: it imports it (`doc` `import`) and gives it the role `comercial`.
- If there is no 3D, the agent models a simplified envelope (id `env_...`, role `comercial`, source in the intent) from dimensions you give or from a published data sheet it cites, never from memory. It serves to locate and check clashes; it is not the real part.
- The agent never asks you to register on a supplier site or give personal data to get a STEP.

### Technical library

The library is your folder of standards (DIN, ISO, NTC), manuals and notes. It lets the agent find and cite a page instead of recalling a figure.

- **You hand over the files; the agent files them.** Give it a PDF, image, `.txt` or `.md` and say "file this standard". It chooses the topic, the name and the page map, and files it with `catalog` `file`. Word, Excel and PowerPoint are not accepted: export to PDF. There is no OCR: a scanned PDF or image is found only through its record card.
- **Where.** `~/.cotaforge/library/`, or the folder in `COTAFORGE_LIBRARY` (for example a team network folder). One folder per topic, ordered by what you look for (`DIN 7979` goes under pins). Topics and synonyms are in `topics.yaml` at its root.
- **Record card.** `<name>.ficha.md` next to each document: title, topic, standard, edition, pages and a page map. You can edit it. The document is copied under a standard name; your original is untouched.
- **Search.** `catalog` `search` also searches the library: record cards first, then page text. The agent opens the page before citing it. Citations read "DIN 7979, 1997, p. 3".
- **Files you copy in by hand** are indexed on the next search. Ask the agent for the card.
- **The index** lives in `COTAFORGE_HOME/indexes/` and can be deleted; it is rebuilt. Full text uses `pypdf`.
- **Never distributed.** CotaForge ships no documents, only code and an initial topic list.

### Open problems: workflows, log and calculations

When a problem has no known architecture, you choose the workflow. `/cotaforge:plan` asks once and stores your preference in user memory. Three starting workflows (the agent may propose a better one and say why):

- **Sketch first**: understand and search, propose a quick sketch in the CAD software, agree it with you, save a copy, pause, then build the detail as an assembly with independent parts.
- **Direct**: for known architectures, almost straight to the model.
- **Systematic design**: the classic method of design textbooks. Functions and alternatives first, then the concept and the detail.

Before the detail the agent pauses to tell you which data are assumptions and to compute what sizes the design.

**`assumptions-and-decisions.md`** is the design log: every assumption, decision and calculation with its source. The agent keeps it and `/cotaforge:resume` reads it. You can edit it.

**`calculations/`** holds the Python scripts that produce the numbers (`calculations/<topic>.py`); the log says which script produced each figure. The first time the agent runs one, Claude Code asks permission. Approve it for `calculations/` after reviewing the script. The design space does not allow it by default because a script written by the agent could bypass the harness.

---

## 8. Save and resume

FreeCAD's native file (`.FCStd`) is the progress of your design. Save with `doc` `action="save"`, resume with `action="open"` or `/cotaforge:resume <file>`.

### What gets saved

Saving writes two files:

- `plate.FCStd`: the FreeCAD document, with the declared material and route.
- `plate.cotaforge.json`: the *session record* (sidecar): step history, identifiers and the measurements at save time.

Keep both together when you copy or move the part. Imported bodies that no operation changed are stored compactly in the record; their faces and edges are regenerated on reopening with the same identifiers.

### On reopening

| Situation | Result |
|---|---|
| Saved by CotaForge, untouched since | The **exact** session is restored: same step and face identifiers, history intact. |
| Edited elsewhere (for example in FreeCAD), or no session record | **Adopted**: opened as is, each body gets an alias to build on. The report warns about *drift* if volume or size changed since the last save. |
| Any `.FCStd` not made with CotaForge | Also adopted. |

`doc` `reload` rereads the active document's file and discards the session's copy. It refuses if there are unsaved changes, unless `force=true`.

Steps from before reopening cannot be undone: the CAD software does not store undo history. You can add new steps and undo those. `/cotaforge:resume` opens the file, reads the report, lists the operation tree, measures and runs `check`, without modifying anything.

### Protections when saving

- A file that is not this document's is not overwritten (`file_exists`). Save under another name.
- A file modified outside since CotaForge last read or saved it is not overwritten (`file_conflict`). Save under another name, `doc` `save` with `force=true`, or `doc` `reload`.
- A document with unsaved changes is not closed (`unsaved_changes`) unless you pass `discard=true`.

Several documents can be open at once; only one is active. If FreeCAD crashes, the next step reopens the last saved active document (with an assembly's parts) and says which others were lost.

### If FreeCAD closes or gets busy

If FreeCAD closes in the middle of a call (you close it, it crashes), that call answers within a couple of seconds with `session_lost`: that FreeCAD is closed, what was lost and how to continue. The **next** call relaunches FreeCAD and reopens the last saved file; that call is not executed, repeat it.

If you cancel a long call, FreeCAD cannot interrupt what it computes. Meanwhile, calls that need the CAD software answer `cad_busy` after a few seconds. Closing FreeCAD frees it (the next call relaunches it and reopens the last save; unsaved work is lost). `memory`, `catalog` and `cad_guide` always respond.

**Recovery copy.** A `run_native` that uses TechDraw has closed FreeCAD in real use. Before running one, if the document has unsaved changes, CotaForge saves a copy in `recovery/` without touching your file. A clean save deletes it. If FreeCAD closes, the copy is kept as `<name>.crash-<time>.FCStd` and the response says what was running and how to continue: `doc` `open` the copy, then `doc` `save` to your file with `overwrite=true`. Assembly copies are not made.

**The drawing review is not lost.** Answers, the chosen reviewer and the reports live in the sheet. They are also noted in `recovery/<name>.revision.jsonl` and put back when the file is opened if the sheet lacks them. The approval ("Approved by") is never restored: it is yours and is asked for again.

---

## 9. Materials, processes and constraints

A **manufacturing process** is how the part will be made: milling, turning, laser cutting, 3D printing. Each machine has limits. Declaring them lets `check` warn when the part cannot be made in your shop.

### Declaring

Declaring is optional. `/cotaforge:declare <what you say>` guides the agent:

1. It reviews what is declared (`memory` `index`).
2. It saves the material or process (`memory` `save`, type `material` or `process`).
3. It assigns it to the open part: `doc` `set_material`, `doc` `set_route` (the ordered list of processes).
4. It runs `check`.

A process entry has a `tag` and capabilities. Known tags: `turning`, `milling_3axis`, `laser_cut`, `sheet_bending`, `fdm`, `sls`, `welding`. Another tag can be used, without automatic checks. Example, `memory/fresadora-taller.md`:

```yaml
---
id: fresadora-taller
type: process
title: Fresadora 3 ejes del taller
applies_when: Piezas prismáticas
not_when: Piezas de revolución
provenance:
  source: shop_measurement
  reference: Ficha de la máquina
  by: Ana
  date: '2026-10-11'
process:
  tag: milling_3axis
  capabilities:
  - name: build_volume_mm
    value_set: [100, 80, 60]
---
```

**Provenance.** A number in memory is a fact and needs `provenance`: `source` (`shop_measurement`, `supplier_datasheet`, `standard`, `user_statement`, `correction`, `reference_book`, `manufacturer_guide`), a `reference`, who states it (`by`) and the date. An entry with numbers and no provenance is rejected. The agent cannot save a figure it estimated. If a datum is missing it asks or marks it `placeholder` (a pending value that never blocks).

### What `check` verifies

`check` compares the measured part with what you declared and gives `pass`, `violation` or `unverified` for each item:

- **Material:** the assigned one is among the declared.
- **Route:** every assigned process is declared.
- **Envelope:** the bounding box fits the process's `max_diameter_mm`, `max_length_mm` or `build_volume_mm`. With several processes in the route, one that holds the part is enough.

```
check: 0 violation, 0 unverified, 3 pass
PASS: material | measured 6061 | limit Aluminio 6061 - ...
PASS: route | measured fresadora-taller | limit fresadora-taller - ...
PASS: envelope.build_volume_mm | measured 60 x 40 x 10 | limit 100 x 80 x 60 - ...
geometry: valid
export: allowed
```

With a violation, `export` answers `export_blocked` and writes nothing. Saving stays allowed:

```
check: 1 violation, 0 unverified, 2 pass
VIOLATION: envelope.max_diameter_mm | measured 250 | limit 200 - ...
export: BLOCKED by 1 violation(s); editing and doc save are still allowed
```

### What `check` does not do

- Diameter and length are deduced from the bounding box, assuming the axis runs along the longest side. If the three sides are within 5% of each other, it marks the item `unverified`. A flat plate against `max_diameter_mm` gives "40": use it with parts that are actually turned.
- Other capabilities you can save (`min_wall_mm`, `min_internal_radius_mm`, `achievable_it_grade`...) are recorded but not checked.
- If the part has `run_native` steps, the envelope checks become `unverified`.
- Tolerances are not verified.

If nothing is declared, `check` says so: nothing restricts the design. A `run_native` fragment that changes the material or route to something undeclared is undone.

### The reviewer

`/cotaforge:review` launches the `reviewer` agent, which measures on its own and can only read: it has no tools to modify the part or save memory. Its report separates **Verified** (backed by a measurement or `check`) from **Judgment** (observations no tool confirmed). Use it before calling a part finished or exporting it. For drawings, the reviewer is the `fabricator` (section 7).

---

## 10. Memory: decisions and corrections

Memory stores what you know and the agent does not: your processes, materials, house rules and corrections. Each entry is a Markdown file with a YAML header. You can edit it by hand.

### Two scopes

| Scope | Where | Use |
|---|---|---|
| `project` (default) | `memory/` in your design space | What applies to this project. |
| `user` | `~/.cotaforge/memory` (or `COTAFORGE_HOME/memory`) | What applies to all your designs. |

If the same `id` exists in both, the project's wins. Each folder has a generated `INDEX.md`: do not edit it. Files are read on every call, so a manual change is seen at once; a malformed file is skipped and reported.

### Entry types

`practice`, `principle`, `process`, `material`, `correction`, `cad_trap` (a known trap of the CAD software) and `case` (a real design and what it teaches). All carry `title`, `applies_when` and `not_when` (mandatory). Only `process` and `material` entries are checked by code; the others are text the agent loads when it applies.

### How it reaches the agent

On startup the server puts the memory **index** (entry ids, grouped by scope and type) in the agent's instructions. When an entry applies, the agent loads it with `memory` `get`. Entries are data, not instructions: they cannot expand what the agent may do. `memory` `index` is always current.

### Who decides what becomes memory

The agent proposes; you decide.

- `save` leaves the entry `status: proposed`. It shows in the index as pending approval and is not pushed or required. `process` and `material` declarations are active when saved.
- You approve by telling the agent. It calls `memory` `approve` (`by` your name, `text` your words). It must not approve on its own.
- Anything the agent saves in `user` stays `proposed`, and the server makes it ask you one question: does this apply to your future designs or only to this project? "For my future designs" activates it in `user` (`approve` with `scope=user`). "Only this project" moves it to the project and activates it there (`scope=project`).
- A `principle` is general. `save` rejects it, and proposes splitting it, if it contains sector or process words, brands, part codes or measurements with units. That goes in a `practice` linked through `related`. A principle also needs `recognize` and at least one example.
- A `practice` with a measurement should carry `sources` or `provenance`; without them `save` stores it with a warning.
- Before writing, `save` looks for similar entries and does not write until the agent updates one (same `id`) or confirms it is new.
- A fact from a single design is not a rule. It goes in the project (a `case` entry or `assumptions-and-decisions.md`). If a `user` entry cites only cases from this project, `save` warns.

### Optional fields

An entry can carry: `recognize` (the situation as seen in the model), `examples` (`{sector, text, validated}`), `typical_error`, `cases`, `related`, `replaces` (the id of a base entry yours replaces), `sources`, `sector` and `when`. `when` sets when the entry is triggered: `roles`, `stages` (`concept`, `layout`, `detail`) and `ops`. If you give several fields, all must match.

### Sector, roles and when memory is pushed

- **Sector and processes.** `memory` `brief` declares them in `brief.yaml` of your design space. The same file stores `drawing_review` (section 7). Declare your sector once: the agent then loads examples from your sector first and marks other sectors' examples as analogies. Without a declared sector, practices are pushed anyway, and examples from several sectors come marked as analogies. A practice from a different sector than yours is neither pushed nor required (it can still be read with `get`).
- **Sector table.** `sectors.yaml` in the base set maps what you write to a sector id (aliases work in English and Spanish). Extend it with your own `sectors.yaml` in the `memory/` folder of your project or user; the project's wins.

```yaml
sectors:
  - id: stamping
    name: Stamping and dies
    aliases: [estampado, troqueles, prensas]
  - id: fixtures
    aliases: [my welding jigs]
```

- **Roles.** Each body or component can have a role: `estructura`, `soporte`, `contacto`, `piloto`, `apoyo`, `calza`, `actuador`, `sensor`, `soldado`, `comercial`, `ajustable`, `fijacion`. The agent sets it in the operation's `role` parameter or with `op` `edit`. It is stored in the file and colors the body (`view` `style` with `color="roles"`). Add roles in `roles.yaml` in your design space: `roles: {soldado: "#a0522d", bastidor: "#336699"}`.
- **At the moment of deciding.** If an operation matches the `when` of an active entry, its result carries a line naming the entry id. It is a signal to load it.
- **At each milestone.** At the end of concept, layout or detail the agent calls `memory` `review` with the stage. The server lists the triggered entries and refuses until each has an answer (`memory` `answer`): `applies: how, on which part` or `does not apply: why`. It accepts an answer only if the agent read that entry (`get`) in this session. Answers are kept in the design state.
- **Measured alerts are not waved away.** Two entries are activated by a measurement of the model: sharp inside corners of a milled pocket, and a bearing on a shaft run much longer than itself. While the measurement still fires, `review` lists them as pending and no answer from the agent closes them, `does not apply: why` included. They close in two ways: the model is fixed (the server measures again and the alert is gone), or you ordered it kept and the agent records your name and your words (`memory` `answer` with `by` and his words, quoted, in the text). The order covers what was measured then; a new violation is open again. A drawing of a part with an open violation is not exported: the sheet shows a requirement "Measured design alerts resolved" that no answer on the sheet closes.

### The base set

The product ships a read-only base set (scope `base`) of general principles and practices by sector. The principles are quality questions for finished drawings and design review. The practices carry the figures a principle cannot, each with its source. Some figures come from specific manufacturers' guides, not standards: confirm them with the shop that will make the part. Entries that cite a source that was not read say so.

Most base practices are read on demand with `memory` `index` or `search`. A few trigger on specific operations or roles. `memory` `index` lists only ids, and a base practice only if it belongs to your declared sector. `memory` `index` with `sector=<id>` lists another sector's practices.

To deactivate an entry in your project, use `memory` `deactivate` with its id. To change one keeping its id, save an entry with that id. If your entry has a different id but says the same thing, declare `replaces: <base id>`. `COTAFORGE_BASE_MEMORY=0` leaves the base set out.

### The plugin commands

| Command | What it does |
|---|---|
| `/cotaforge:declare <text>` | Declares materials or processes as a hard constraint (section 9). |
| `/cotaforge:fix <text>` | Saves your correction as a `correction` entry. It searches for similar entries first, uses only what you said, requires provenance for numbers, saves in the project, and proposes the `user` scope (one question) if the lesson does not depend on this project. |
| `/cotaforge:plan <problem>` | Chooses the design workflow with you (section 7). |
| `/cotaforge:resume <file>` | Resumes a `.FCStd` and summarizes its state (section 8). |
| `/cotaforge:review <focus>` | Independent review (section 9). |
| `/cotaforge:friction <text>` | Writes a note in `friction.md` of your project. |

Example:

> `/cotaforge:fix` The chamfer on a threaded hole is made after tapping, never before. It applies to all my designs.

The agent saves a `correction` in `user` as a proposal, asks once whether it applies to all your designs, and with your yes activates it.

### Deactivating

`memory` `deactivate` marks an entry inactive: the file stays, but it stops constraining and appearing in the index. In `user` scope it is switched off for all your designs. To switch off a user entry in one project, save an inactive entry with the same `id` in that project.

---

## 11. `run_native`, the emergency exit

`op` operations are verified and editable but do not cover everything. `run_native` runs a fragment of FreeCAD Python as one undoable step. The agent must read `cad_guide` with `topic="native"` first and give the reason in `intent`. The fragment has `doc`, `App`/`FreeCAD`, `Part`, `Base`, `Vector`, `Placement`, `Rotation`, `Sketcher`, `PartDesign` and `math` available. Everything else is imported. There is no GUI.

```
ok: thick native
modified: Body (PartDesign::Body), Pad (PartDesign::Pad)
volume change: +36000 mm3
bbox: size (60, 40, 25) mm, min (0, 0, 0), max (60, 40, 25)
```

`undo` undoes it like any step.

### Automatic undo on failure

The step is reverted (`state: unchanged`) when the code raises an exception, exceeds `timeout_s` (60 s by default, 600 s maximum) or leaves a broken object: null or invalid shape, empty boolean, zero-volume solid, conflicting sketch constraints, a feature built on a sketch not fully defined by dimensions, or a recompute error.

```
s3 native was not applied
error: BrokenResult: the step left 1 object(s) broken: C: boolean produced an empty result
state: unchanged
```

A fragment that changed nothing is not recorded. Objects a fragment creates are not operations: operations cannot build on them by step reference.

### Sketches behind a feature are fully defined

A feature is built only on a sketch **fully defined by dimensions**. The `op` sketch always is. A sketch drawn by hand or by `run_native` may have freedom.

- A `run_native` step that builds a feature on such a sketch is reverted. The response says how many degrees of freedom remain and which geometry is free. Dimension the sketch before creating the feature. A half-finished sketch with no feature on top is accepted.
- A `Block` constraint does not count as a definition.
- A file you open keeps its sketches as they are. `check` lists the sketches under a feature that are not fully defined. Changing a property of an existing feature is allowed. Building a new feature on such a sketch, or leaving it with freedom, is rejected until it is dimensioned.
- A sketch no feature uses may have freedom.

### Why it sometimes asks you: the hook

A Claude Code hook is a program that runs before a tool. `cotaforge init` registers one for `mcp__cad__run_native` that scans the code first. If it finds something, Claude Code asks you and shows the reasons:

```
run_native: the fragment does something worth a look (advisory scan, not a sandbox)
line 1: imports 'os' (reaches the file system and processes)
line 2: calls os.system(...) (runs programs or removes/renames files)
```

It flags importing `os`, `subprocess`, `shutil`, `socket`, `urllib`, `http`, `requests`, `ctypes` or `importlib`; `eval`/`exec`/`compile` of non-literal text; `__import__`; and file writes whose destination is not provably inside your design space. Reading files is not flagged. Harmless code passes without asking.

**It is a heuristic, not a sandbox.** It reads the code's text; anything Python does dynamically escapes it. A clean scan does **not** mean the code is safe. The hook only turns findings into a question. It never blocks and never replaces your permission rules. If you approve, the code runs in the FreeCAD process with your user's permissions. If the hook itself fails, it prints nothing and does not block modeling.

### TechDraw scripts

`makeCosmeticLine` and `formatGeometricEdge` closed FreeCAD in real use (exit code `3221226356 = 0xC0000374`) and unsaved work was lost. The crash could not be reproduced, so the cause is unknown, and the `run_native` warning says so. These calls are not forbidden: a script that uses TechDraw first gets a recovery copy (section 8). For centre lines and dashed lines, use the `line` element of `drawing` instead.

### Usage log

Every `run_native` call is noted in `native-usage.jsonl` in your design space. The code itself is not stored.

---

## 12. Limits

- **One CAD software: FreeCAD 1.1.x.** The `fake` adapter is a test simulator.
- **Only from Claude Code** (terminal or the Code tab). There is no interface of its own.
- **Without a window there is no 3D capture or selection.** `view` `capture`, `set_view`, `style`, `section` and `selection` need `--visible` and a display. `drawing` `capture` works without a window.
- **Operations that do not exist:** loft and revolved cut (revolve a body and subtract it with `boolean_subtract`). Sketches support rectangle, square, circle, `slot`, and chained lines and arcs in closed profiles (`arc3`, `arc_ends`, `arc`).
- **Drawings:** views, dimensions, hole callouts, position dimensions, notes, revisions and title block; ISO 286 fits and ISO 2768 general tolerances (transcribed tables, not verified), ISO 1101 geometric tolerances, datums, surface finish and ISO 6410 threads. **Not supported:** welding, broken views, partial sections, bend lines in sheet metal. The title block text defaults to Spanish.
- **No stress calculation.**
- **Sheet metal** exists only with the SheetMetal add-on and with the limits in [its section](#sheet-metal-optional).
- **`check`** covers material, route and envelope (section 9).
- **Assemblies:** no subassemblies (only parts are inserted). Face mates move the instance without leaving a live relation in FreeCAD. Joints are `fixed`, `revolute` and `slider`; the value is set by CotaForge, not FreeCAD's solver, and an instance hanging from a moving one does not follow it. The assembly shows only what is saved of each part.
- **Features of an opened file cannot be undone.** They are adopted as bodies and you build on them.
- **Tool results are in English.** The agent explains them in your language.
- **`run_native` is not a sandbox** (section 11).
- **The agent's judgment is not verified.** The numbers come from the CAD software; whether a part is a good idea does not.
- **Memory entries are your data, not truth.** A bad entry is taken as data.

---

## 13. Glossary

Terms of the product itself.

| Term | Meaning |
|---|---|
| **Design space** | The folder created by `cotaforge init`. |
| **Operation** | One modeling step. Each `op` call is one. |
| **Plan** | The list of operations with their parameters (`inspect` `tree`). |
| **Intent** | The "why" line stored with each step. |
| **Adapter** | The CAD back end: `freecad`, or `fake` for tests. |
| **Declared** | Material or process you recorded. A declaration is a hard constraint. |
| **Closed world** | If you declare something of a type, only what is declared is valid. |
| **Process route** | The ordered list of processes assigned to a part. |
| **Provenance** | The origin of a number: source, reference, author, date. |
| **Session record** (*sidecar*) | `<name>.cotaforge.json` next to the `.FCStd`. |
| **Drift** | Difference between what CotaForge saved and what is in the file now. |
| **Adopt** | Opening a file without its history and building on its bodies. |
| **Scope** | Where a memory entry lives: `project`, `user` or `base`. |
| **Proposed** | A memory entry waiting for your approval. |
| **Fabricator** | The agent that reviews a drawing as the person who makes the part. |
| **Drafter** | The agent that makes drawings in its own session. |
| **Sheet** | One drawing. One sheet is exported to one file. |
| **Native** | A TechDraw object you can edit in FreeCAD, as opposed to a drawn symbol. |
| **Hook** | A Claude Code program that runs before a tool. |
| **`run_native`** | The escape hatch: a FreeCAD Python fragment as one undoable step. |

---

## 14. Common problems

| Symptom | Cause and solution |
|---|---|
| `init` warns "FreeCAD not found", or `the CAD software did not start: ...`, or `FreeCAD root not found` | CotaForge cannot find FreeCAD's root. Pass the folder with `bin\FreeCADCmd.exe` (Windows) or `usr/bin/freecadcmd` (Linux) via `--freecad-root`, or set `COTAFORGE_FREECAD_ROOT`. Run `cotaforge init` again and restart Claude Code. |
| Windows: `... is too long for FreeCAD on Windows: <path> (N characters; the limit is 209, ...)` | Windows limits paths to 260 characters and FreeCAD writes temporary files next to the destination, so CotaForge rejects any path of 210 characters or more. Use a shorter folder or file name. If the warning names the FreeCAD folder, the worker script or the worker log, the clone or the temporary folder is in a path that is too long: clone into a short path such as `C:\dev\cotaforge`, recreate the virtual environment there and run `cotaforge init` again. Enabling Windows long paths is not verified to fix it. |
| Windows: `the CAD software did not start: worker failed to start: worker exited (code 0) while waiting for hello ...` | Seen with a clone in a path of about 270 characters. Current versions show the path-too-long warning instead. If you still see it, check the worker log (temporary folder, inside `cotaforge`) and clone into a short path. |
| The `cad` server does not appear in `/mcp` | You opened Claude Code in another folder: it must be the design space (where `.mcp.json` is). Or the virtual environment was moved or deleted: run `cotaforge init` again (installer: **Open existing project (CotaForge)**). If Claude Code asks to approve the server, approve it. |
| Windows says "Windows protected your PC" | The installer is not signed with a paid certificate. Click **More info**, then **Run anyway**. Do so only with an installer from a source you trust. |
| `/cotaforge:` does not appear, or the plugin is missing in the Code tab | The plugin is not registered. Run `cotaforge plugin status` (installer: `python.exe -m cotaforge_mcp.cli plugin status`, section 4.1). If it says "NOT registered", run `plugin register` or use any CotaForge shortcut. If it cannot find Claude Code, install it and sign in. Restart the Claude Code session afterwards. |
| The shortcuts say "I could not find Claude Code" | Claude Code is not installed, or it is somewhere neither the `PATH` nor `%USERPROFILE%\.local\bin` covers. Install it (<https://claude.com/product/claude-code>) and open the shortcut again. |
| After updating, an old project does not connect | Open it with **Open existing project (CotaForge)**. It refreshes `.mcp.json` and the hook. Your memory and `CLAUDE.md` are not touched. |
| `no document is open` | Ask the agent to create (`doc` `new`) or open one. |
| `a document named '...' is already open` | Use another name or `doc` `switch`. |
| `... was not applied`, `state: unchanged` | Normal: the operation could not be applied and nothing changed. The agent reads the reason and corrects it. |
| `export_blocked` | The part violates what you declared. Run `check`, fix the part or change the declaration (`/cotaforge:declare`). |
| `outside_declared` | You asked for a material or process you did not declare (closed world). Use a declared one or declare it first. |
| `file_exists` or `file_conflict` when saving | The name exists or the file changed outside. Save under another name. |
| `file_exists` when exporting | The file exists and is not overwritten by default. Use another name or `overwrite=true`. |
| `base_flange`, `edge_flange` and `unfold` missing, or `the SheetMetal add-on is not installed` | Install the add-on from the Addon Manager and restart FreeCAD and Claude Code (section 7). If it says `cannot be loaded`, update or reinstall it. |
| An `unfold` is undone with a different `flat_length_mm` | The flat pattern FreeCAD measured does not match the bend formula. Check the `k_factor`, radius and angle. A flat pattern that does not add up is not delivered. |
| `degraded_session` | The CAD session is unusable. Save if you can, close the document and, if it persists, restart Claude Code. |
| `session_lost` | FreeCAD closed during the call. The response says what was lost and where the recovery copy is (section 8). Repeat any call: it relaunches FreeCAD and reopens the last save. |
| `cad_busy` | FreeCAD is still on a previous call (typically one you cancelled). Wait, or close FreeCAD: the next call relaunches it and reopens the last save (unsaved work is lost). |
| `run_native` does not ask | Expected for harmless code. If you expected a question, check that `.claude/settings.local.json` still has the hook (run `init` again if not). The scan is a heuristic (section 11). |
| The hook fails because it cannot find Python | The environment `init` was run with no longer exists. Run `init` again (installer: **Open existing project (CotaForge)**). On Windows hooks run through Git Bash, so Git for Windows is required. If the installation path has spaces, `init` writes its short 8.3 form when it exists. |
| "Not logged in" | It comes from Claude Code. Sign in with `claude` and `/login`. |
| My global instructions (`~/.claude/CLAUDE.md`) do not apply in the design space | Intentional: `init` excludes inherited `CLAUDE.md` files. Remove the exclusion from `claudeMdExcludes` in `.claude/settings.local.json` if you want them. |
| The FreeCAD window does not appear | It appears only with `--visible` (run `init` again with the option) and needs a display. The first connection may take a few seconds. |
| A memory file "could not be loaded" | `memory` `index` lists files with problems and skips them. Fix the YAML or delete the file. |

---

## 15. How to give feedback

Open an issue: <https://github.com/Jomruizgo/cotaforge/issues/new/choose>.

Attach:

- The versions of CotaForge (`cotaforge version`), Claude Code and FreeCAD.
- What you asked the agent.
- The exact error or tool reply.

Never attach a customer's file.

---

## 16. Quick reference sheet

### Terminal commands

| I want to... | Command |
|---|---|
| Install (Windows) | Run `cotaforge-<version>-setup.exe` (section 4.1) |
| Install (clone) | `python -m pip install -e kernel -e mcp-server` |
| Register, remove or check the plugin | `cotaforge plugin register` / `unregister` / `status` |
| See the installed versions | `cotaforge version` |
| Create a design space | `cotaforge init <folder>` (Windows: the "Create new project (CotaForge)" shortcut) |
| ...with FreeCAD in another path | `cotaforge init <folder> --freecad-root <path>` |
| ...with a FreeCAD window | `cotaforge init <folder> --visible` |
| ...without a window | `cotaforge init <folder> --no-visible` |
| Replace `CLAUDE.md` | `cotaforge init <folder> --force` |
| Open Claude Code with the plugin registered | Desktop app: Code tab, choose the folder. Or `cd <folder>` and `claude` |
| ...without registering the plugin | `cd <folder>` and `claude --plugin-dir <clone>/plugin` |

### Environment variables

| Variable | Effect |
|---|---|
| `COTAFORGE_FREECAD_ROOT` | FreeCAD root. |
| `COTAFORGE_VISIBLE` | `1` opens FreeCAD with a window. |
| `COTAFORGE_WORKSPACE` | Design space folder (written by `init`). |
| `COTAFORGE_ADAPTER` | `freecad` (default) or `fake`. |
| `COTAFORGE_HOME` | Replaces `~/.cotaforge` (user memory, your catalog, library index). |
| `COTAFORGE_LIBRARY` | Your technical library folder, instead of `~/.cotaforge/library`. |
| `COTAFORGE_BASE_MEMORY` | `0` leaves out the base set. |
| `COTAFORGE_FREECAD_MOD_DIRS` | `Mod` folders to check for the SheetMetal add-on. |
| `COTAFORGE_IMPORT_STALL_S` | Seconds an import can go without FreeCAD using CPU before it is cut off (default 120). |
| `COTAFORGE_IMPORT_MAX_S` | Total cap for an import, in seconds (default 3600). |

### Commands inside Claude Code

| Command | Purpose |
|---|---|
| `/mcp` | Check that the `cad` server is connected. |
| `/cotaforge:declare <text>` | Declare materials or processes. |
| `/cotaforge:plan <problem>` | Choose the design workflow. |
| `/cotaforge:resume <file>` | Resume a `.FCStd`. |
| `/cotaforge:review <focus>` | Independent review. |
| `/cotaforge:fix <text>` | Save a correction in memory. |
| `/cotaforge:friction <text>` | Writes a note in `friction.md` of your project. |

### Phrases that work

| I want... | Tell it |
|---|---|
| A declarative part | "60 x 40 x 10 plate in 6061 aluminum, 3-axis milling machine, Ø8 through hole centered on the top face. Verify with `check`." |
| To see the plan | "Show me the operation tree." |
| To measure | "Give me the volume and bounding box." |
| To undo | "Undo the last step." / "Go back to step `s2`." |
| To save | "Save as `plate.FCStd`." |
| To point at a face (with `--visible`) | Select it in FreeCAD and say "Ø6 hole on the face I selected". |
| To point at a dimension, view or hole on the drawing (with `--visible`) | Select it in the sheet window and say "what does this dimension measure?". To find the dimensions of a face: select it in 3D and ask "which dimensions refer to this face?". |
| For the agent to show you which one (with `--visible`) | "Show me dimension d3." |
| To export | "Export to STEP." |
| A drawing | "Set up the drawing on A3 at 1:2 with the title Bracket, dimension the holes and their position from the corner, and export it to PDF and DXF." |
| To see inside (with `--visible`) | "Set the box to 70% transparency." / "Cut a plane at z = 10." |
| A review | `/cotaforge:review` |

The tools of the `cad` server and their actions are in [section 7](#7-how-the-agent-works).
