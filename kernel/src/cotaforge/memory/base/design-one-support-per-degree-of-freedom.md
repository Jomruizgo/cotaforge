---
id: design-one-support-per-degree-of-freedom
type: principle
title: Each degree of freedom is constrained once, neither one fewer nor one more
applies_when: When two bodies must stay in a defined relative position, or a body rests on a structure
not_when: When an elastic average with many contacts is sought on purpose, or a joint that by design deforms to seal
recognize: More contacts than degrees of freedom to constrain; a fourth support, a third bearing, two rigid guides in parallel
typical_error: Adding a redundant contact for safety; the part comes to depend on the manufacturing tolerance, deforms when tightened or changes with temperature
examples:
- {sector: fixtures, text: "Four fixed supports under a rigid part: only three touch it and the fourth becomes a shim. Three fixed and one adjustable are used.", validated: false}
- {sector: power-transmission, text: "A shaft with three rigid bearings in the same housing: the misalignment overloads them. Two supports are enough except on a very long shaft.", validated: false}
- {sector: special-machines, text: "Two rigid linear guides in parallel: one defines the position and the other is left compliant in the transverse direction.", validated: false}
- {sector: welded-structures, text: "A piece of equipment on four floor supports: three define the plane and the fourth is adjustable.", validated: false}
sources:
- "MIT OCW 2.75 (Culpepper), Design of constraints in precision systems, 2001: exact constraint, constraint points equal to the degrees of freedom (read)."
- "MIT 2.70 (Slocum), Fundamental principles, 2020: 6-N reactions; do not constrain a shaft with more than 2 supports unless it is very long (read)."
- "Blanding, Exact Constraint: Machine Design Using Kinematic Principles (ASME Press, 1999): book not opened."
when:
  roles: [apoyo, contacto]
  stages: [layout, detail]
  ops: [mates]
---
