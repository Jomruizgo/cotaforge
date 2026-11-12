---
id: machining-inner-radius-and-cavity-depth
type: practice
title: A milled pocket or slot cannot have sharp vertical inside corners; the radius follows from the tool that reaches its depth
sector: precision-machining
applies_when: When modeling a pocket or a slot cut into a part that is made by milling
not_when: A pocket made by electrical discharge machining; a broached shape; a profile cut through a plate by laser or waterjet; a corner relieved on purpose (a dog-bone or T-bone); a corner that must stay sharp and the shop knows it (EDM or a mating relief)
recognize: A pocket or a slot whose vertical inside corners have radius 0, or a radius smaller than the tool that can reach the depth of the pocket
examples:
- sector: precision-machining
  text: 'A 12 mm deep pocket drawn as a plain rectangle: its four vertical corners are 90 degrees and no end mill can make them; they get a radius a little larger than the tool that reaches 12 mm.'
  validated: false
- sector: precision-machining
  text: 'A slot 3 mm wide and 20 mm deep: the end of the slot is as small as the tool that fits in it, and a tool that thin cannot be that long; the slot is made wider or less deep.'
  validated: false
- sector: precision-machining
  text: 'A square hole that a part with a square corner must enter: the corners of the hole get a relief (dog-bone) so that the sharp corner of the other part fits.'
  validated: false
typical_error: A sharp inside corner in a milled pocket that the end mill cannot make without rounding it or without a fragile tool
related:
- design-tool-reaches-the-feature
sources:
- "Protolabs Network (formerly Hubs), \"How to design parts for CNC machining\", knowledge base page, no edition or date shown, read 2026-11-12: recommended inside vertical radius of one third of the cavity depth or more; a corner radius slightly above the recommended value (for example by 1 mm) lets the tool follow a circular path instead of cutting a 90-degree angle; cavity depth up to 4 times the width; cavities deeper than 6 tool diameters are deep; floor radius 0.5 mm, 1 mm or none."
- "Protolabs Network (formerly Hubs), \"How do you deal with sharp corners in CNC machining? Designing with the machinist in mind\", knowledge base page, no edition or date shown, read 2026-11-12: the minimum internal corner radius achievable in CNC machining equals the cutter radius; cavity depth of two to three times the cutter diameter; corner radius of at least one third of the depth; T-bone and dog-bone reliefs add clearance equal to the cutter radius; EDM reaches internal corners down to R0.02 mm."
---
- The vertical inside corner of a milled pocket or slot is never 90 degrees: it has at least the radius of the end mill. The end mill must be long enough for the depth, and a usual stiffness limit is a depth of about 3 to 4 times the tool diameter. So the smallest tool radius for a depth is about depth / 8 (at 4 times) to depth / 6 (at 3 times).
- Make the corner radius a little larger than the tool's, not equal: with an equal radius the tool wraps the corner, cuts with its whole side and chatters. The shop guidance of one third of the depth (or more) leaves that margin.
- The floor corners follow the end shape of the tool: a flat end mill leaves a small radius (0.5 mm or 1 mm) or none; any other floor radius needs a ball-end tool.
- If the function needs a sharp corner (a mating part with a square corner enters it), relieve the corner (dog-bone or T-bone, as deep as the cutter radius) or change the process (EDM).
- These are guidelines from a specific manufacturer: confirm the tool and the radius with the shop that will make the part.
- Server warning: for a pocket or a slot made by a cut, when it measures vertical inside corners with radius 0, or a radius smaller than depth / 8, it returns a `memory:` line with the measurements. It is a warning, it blocks nothing. It looks only when the project processes include milling or machining, or none is declared (a cut that goes through the part is looked at only when milling or machining is declared). It does not see a corner relieved on purpose.
