---
id: design-free-expansion-at-one-end
type: principle
title: Every long part or part with a thermal gradient is fixed at one point and leaves the expansion free
applies_when: When a long part or a structure works with temperature differences or a material different from its support
not_when: The temperature variation is negligible against the tolerance of the part
recognize: A long part fixed axially at both ends, or supported on a material with a different expansion
typical_error: Fixing both ends; the part buckles or overloads the supports when it heats up
examples:
- {sector: power-transmission, text: "A shaft carries one bearing that fixes the axial position and another that allows axial sliding.", validated: false}
- {sector: special-machines, text: "A long steel rail on an aluminum frame is fixed at one point and allowed to slide elsewhere.", validated: false}
- {sector: hygienic-equipment, text: "A hot pipe rests on supports that allow expansion and do not retain dirt.", validated: false}
- {sector: welded-structures, text: "A long frame is anchored at one point and rests on sliding supports elsewhere.", validated: false}
sources:
- "MIT 2.70 (Slocum), Fundamental principles, 2020: a shaft constrained axially at both ends can buckle; different materials and symmetry; back-to-back versus face-to-face bearings (read)."
- "EHEDG Doc 8 (2004), section 6.7: supports do not retain water or dirt (read)."
when:
  roles: [estructura, soporte, apoyo]
  stages: [layout]
  ops: [mates, joints, fix_component]
---
