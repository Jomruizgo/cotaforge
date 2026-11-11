---
name: resume
description: Resume a design from a native file of the CAD software (.FCStd) and summarise its state. Use it when the engineer asks to continue, open or resume a file.
---

Resume the file the engineer names (`$ARGUMENTS`). If he named none, ask which one (a single question). Speak to the engineer in the project language (`language` in `memory(action=brief)`; Spanish if none is set).

1. `doc` with `action="open"` and the file's `path`. It accepts any `.FCStd`, also one not created with this harness.
2. Read the opening report: if it mentions drift or adopted bodies, say so as it is.
   If it says it put back answers or reports of the drawing review (`put back from the review journal`), tell him: they were in `recovery/` and not in the file, and they are unsaved. If `recovery/` (`recuperacion/` in an older project) holds a `<name>.crash-*.FCStd` newer than the file (FreeCAD closed during a script and that copy has the unsaved work), say so and ask whether to open that copy; do not open it without his answer.
3. `inspect` with `action="tree"` to see the operations, and `inspect` with `action="properties"` for volume and box.
4. `check` to see whether the design violates what was declared (material, processes, constraints).
5. Read `assumptions-and-decisions.md` of the design space (if it exists; `supuestos-y-decisiones.md` in an older project): assumptions not yet confirmed, decisions taken and calculations with their script in `calculations/` (`calculos/` in an older project).
6. Summarise in a few lines: what there is, what it measures, what it violates, which assumptions are still open and what is missing. Every number comes from the tools.

Change nothing when resuming. The parts adopted from the file cannot be undone: warn the engineer if he asks to undo anything from before the opening.
