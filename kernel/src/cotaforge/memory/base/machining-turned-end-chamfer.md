---
id: machining-turned-end-chamfer
type: practice
title: The free ends of a turned part get a chamfer or a radius; a bare 90 degree edge is a design fault
sector: precision-machining
applies_when: When modeling a turned part (a shaft, a pin, a bushing) and when giving its ends their final form
not_when: An end that must stay sharp on purpose (a seal seat, a press-in edge the engineer ordered kept); a part that is not turned; a disc, a washer or a flange (short against its diameter); a bought part (catalog item)
recognize: A turned part whose end face meets the outer cylinder at a bare right angle, with no chamfer and no radius, including the end that carries a thread
examples:
- sector: precision-machining
  text: 'A stepped shaft with a chamfered end at the bearing and a bare end at the other side: the bare end has no lead-in, so the part that must slide onto it catches on the edge, and it carries a burr the shop has to break by hand; it gets the same kind of chamfer.'
  validated: false
- sector: precision-machining
  text: 'A shaft end that carries a thread: the chamfer goes at least down to the thread minor diameter, so that the first thread starts and the nut or the tapped part enters.'
  validated: false
typical_error: A turned end left as a bare edge in the model, so the drawing has to say "break sharp edges" for something that is a design decision (lead-in for assembly, safety, burr)
related:
- design-assembly-only-one-way
sources:
- "Protolabs Network (formerly Hubs, sold as Protolabs CNC), \"Design Guidelines: CNC Turning\", web page, no edition or date shown, read 2026-11-12: \"Turned outside corners will typically include a 0.005 in. chamfer\"; edges of steel parts can be broken (deburred). The page does not say anything about shaft ends or lead-ins: that reasoning is design practice, not from this source."
---
- Every free end of a turned part gets a chamfer (a typical one is 0.5 to 1 mm at 45 degrees, smaller for small diameters) or a radius. It is a lead-in for assembly, it removes the burr and the cutting edge, and it is made in the same set-up as the turning.
- A threaded end needs it too: the chamfer reaches down to the thread minor diameter so that the first thread starts.
- Decide it in the model, not with a general note on the drawing: a chamfer that is modelled is dimensioned on the sheet and the reviewer finds it.
- A chamfer on a shoulder or on the edge of a bore is a separate decision; this entry is about the two free ends.
- Server warning: when a turned part (a body that is mostly cylinders and planes of one axis, at least as long as its diameter) has a free end whose outer edge is a bare right angle between the end face and the cylinder, it returns a `memory:` line per end, with the diameter and where the end is. It is a warning on measured geometry: it closes when the end is chamfered (the harness measures again) or when the engineer orders it kept (his name and his words, quoted). It does not see the edge of a bore, a shoulder, an end that is not planar, or a chamfer given only as a drawing note. It looks only when the project processes include turning or machining, or none is declared.
- The size above is a common value, not a rule of the source cited: confirm it with the shop and the fit.
