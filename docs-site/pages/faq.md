# FAQ and limits

## Cost and privacy

CotaForge is free and open source (MIT). Claude Code needs a Claude plan. Drawings and reviews use extra agents, so they use more of your limit.

Your files are on your computer. For what Claude sees, read [what you need](what-you-need.md#what-claude-sees).

## Versions

FreeCAD 1.1 only. Windows 10 and 11; Linux from source. Claude Code only.

## Limits

- Without the FreeCAD window there is no 3D picture and no selection by pointing.
- No loft.
- Drawings do not cover welding symbols, broken views, partial sections or bend lines.
- Assemblies have no sub-assemblies. Joints are fixed, revolute and sliding.
- Sheet metal needs the FreeCAD SheetMetal add-on.
- `check` compares the part with the material, process and size you declared. It does not calculate stress or verify tolerances.
- The agent's judgment is not verified. Whether a part is a good idea is for you to decide.

More in the [manual](manual.md#12-limits).

## Common problems

| What you see | What to do |
|---|---|
| "Windows protected your PC" | **More info**, then **Run anyway**. |
| `/cotaforge:` shows no commands | Click any CotaForge shortcut in the Start menu, then restart Claude Code. |
| The `cad` server is not connected (`/mcp`) | You opened another folder. Open your project folder. |
| FreeCAD is not found | Install FreeCAD 1.1 in its usual folder and open the project again. |

More in the [manual](manual.md#14-common-problems).

## Report a problem

[Open an issue](https://github.com/Jomruizgo/cotaforge/issues/new/choose). Attach the CotaForge, Claude Code and FreeCAD versions, what you asked and the exact error. Never attach a customer's file.

Security problems go in private: [SECURITY.md](https://github.com/Jomruizgo/cotaforge/blob/main/SECURITY.md).
