---
name: plan
description: Plan how to approach a new design or an open problem. Choose the workflow with the engineer and remember his preference. Use it when starting a design that is not a trivial part.
---

You are about to start a design (`$ARGUMENTS`). The engineer chooses the workflow; those below are reference, not mandate. Speak to the engineer in the project language (`language` in `memory(action=brief)`; Spanish if none is set), and write what he reads or edits (the register, calculation output, memory entries) in it too.

1. Look at `memory` with `action="index"`: if a design-workflow practice already exists in the `user` scope, use it and do not ask.
2. If none, ask once which he prefers. If he has no preference, propose one for the problem (known architecture or exploratory) and say why.
3. Save the choice with `memory` and `action="save"`, `scope="user"`, `type="practice"`, with `title`, `applies_when` (e.g. "when starting a new design") and `not_when` (e.g. "when the engineer asks for another workflow for this project"). Only what the engineer chose; the `user` entry stays `proposed` until he answers the question the server returns (does this hold for his next designs or only this project?) and approves it (`approve` with `scope="user"` or `scope="project"`, only on his order).
4. Also ask him the sector and processes of the project and declare them with `memory` and `action="brief"`.

## Starting workflows
- **Sketch first**: understand and research what is needed (no long report); propose early with a sketch in the CAD software, not with text; discuss and agree it; save a COPY of the agreed sketch; pause (see below); build the detail as an assembly with each part independent. In the sketch fast is fine (boxes, cylinders, booleans) and moving mechanisms are checked at their extreme positions. No paperwork before drawing.
- **Direct**: known architectures; almost straight to the model.
- **Full methodical**: functions, alternatives and morphological matrix before the sketch and the detail (Pahl and Beitz).

If you see a better workflow for this problem, propose it and say why; the engineer decides.

## Before modelling: how it is made and assembled
Unless the part is trivial, before the first sketch write in `assumptions-and-decisions.md`, and show the engineer, with measures and the reason for each decision: (1) the parts, one by one, and how each is made: machined, cut, cast, welded or commercial; the process changes the model, a weldment is separate bodies and not one solid; (2) how each part is located against the others before joining them, because location is not left to assembly, and how it is joined; (3) which surfaces are machined, and whether before or after welding; (4) each fit and tolerance that guarantees the assembly, and why; (5) the edge, corner and joint details that matter for making and assembling it; (6) what the drawing needs: sheets, views, how holes are located, welds; (7) what you ask the engineer; whatever he does not answer stays as an assumption.

## Always
- Restate the problem as functions and physical phenomena and say the analogies aloud.
- Pause before the detail: say which data are assumptions (and ask the engineer) and calculate what sizes the design.
- Record assumptions, decisions and calculations in `assumptions-and-decisions.md`, each with its source (catalog and standard, user catalog with supplier and date, web with URL and date, or assumption).
- Plates and parts cut from sheet or plate: 45 degree chamfer on the plan-view contour corners by default (see `CLAUDE.md`, Conduct); noted as an assumption and omitted only for a real function of that corner, named in the register.
- Commercial first (see below): in the pause before the detail, list the parts that transmit, guide, seal, fasten or support in a standard way and look them up BEFORE modelling them.
- Calculations in Python, saved in `calculations/<topic>.py`, never a loose `python -c`; the register points to the script. Excel only as output for the engineer, if any.
- A datum that is not in a catalog is searched on the web and marked as such.

## Commercial first
A part that exists on the market is bought, not drawn: bearings, pillow blocks, couplings, seals, keys, retaining rings, springs, gearmotors, fasteners. Before modelling such a part:
1. Look it up with `catalog` (`search`: standard tables, the engineer's catalog and the technical library), and if it is not there, on the web. If the engineer has the standard or the supplier's catalog, ask him for the file.
2. Leave the trace in `assumptions-and-decisions.md`, in the section «Commercial parts», one line per part: `- <part and function> | searched: <query> in <catalog | library | web URL, date> | found: <reference, standard or "nothing"> | decision: <buy | make: why> | 3D: <engineer's STEP | simplified envelope, source | n/a>`. Write it also when the decision is to make it: it is what allows reviewing it.
3. If you decide to buy, the part enters the model with role `comercial`. Without a 3D at hand there are two ways:
   - The engineer provides a STEP: `doc` with `import`, then `op(edit=<adopted body>, params={"role": "comercial"})`. Insert that document in the assembly.
   - No 3D: model a simplified envelope (cylinders, boxes) with id `env_<part>`, role `comercial` and an `intent` that says "simplified envelope" and the source. The measures come from the engineer or from a published data sheet you cite (URL, page, date), never from your memory; if a measure is missing, ask him. An envelope is not the real part: on the drawing it is described by its supplier designation or its standard.
4. Never ask the engineer to register on a supplier's site or give personal data to download a STEP. If a site requires them, say so and go to the next source (another supplier, a parts library, the engineer's folder) or to the simplified envelope.
