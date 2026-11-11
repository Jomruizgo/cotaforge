---
name: declare
description: Declare the available materials or manufacturing processes as a hard constraint of the design. Use it when the engineer says which material or process the part must be made with.
---

The engineer declares a material or a process (`$ARGUMENTS`). Declaring is optional, and what is declared is a hard constraint: the design may use a subset of what is declared and never anything outside it. Speak to the engineer in the project language (`language` in `memory(action=brief)`; Spanish if none is set).

1. Look at what exists with `memory` and `action="index"`; do not duplicate an entry.
2. If the engineer gave data (capabilities of a process, properties of a material), save it with `memory` and `action="save"` as an entry of type `process` or `material`. Every number carries `provenance` (data sheet, standard, shop measurement or what the engineer stated, with `by` and the date). Never invent or estimate a capability: if a value is missing, mark it `placeholder` or ask.
3. Apply it to the open part with `doc`: `action="set_material"` with the material and `action="set_route"` with the ordered list of processes.
4. Run `check` and say in one line whether the current design complies or what it violates.

If the engineer asks for something outside what is declared, do not do it: tell him what the constraint is and what alternatives exist inside what is declared.
