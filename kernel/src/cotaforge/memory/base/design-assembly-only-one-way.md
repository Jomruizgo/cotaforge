---
id: design-assembly-only-one-way
type: principle
title: Each part enters in a single position and orientation; what cannot be assembled wrongly is not assembled wrongly
applies_when: When a part is symmetric or nearly symmetric and an incorrect orientation is possible
not_when: The part is symmetric on all axes and any orientation works the same
recognize: A nearly symmetric part that fits in two orientations, one of which is incorrect
typical_error: Trusting the operator to tell nearly identical faces apart or relying on color marks
examples:
- {sector: fixtures, text: "A pin or an asymmetry in the support prevents loading the part turned, without hindering the correct load.", validated: false}
- {sector: special-machines, text: "The fittings or connectors of different axes have different sizes or positions so they cannot be swapped.", validated: false}
- {sector: sheet-metal, text: "An asymmetric notch in a tab allows assembly only in the correct orientation.", validated: false}
- {sector: injection-molds, text: "Interchangeable inserts carry an offset pin so they are not fitted backwards.", validated: false}
sources:
- "Applied Engineering, Jig and Fixture Design (summary of Hendriksen, 1973): notches, contours, pins and blocks that prevent incorrect loading (read)."
- "Shingo, Zero Quality Control: prevention in design; book not opened, only a secondary summary."
when:
  roles: [contacto]
  stages: [detail]
  ops: [insert_component, place_component]
---
