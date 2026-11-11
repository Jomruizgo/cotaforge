---
id: design-distortion-from-heat-or-forming
type: principle
title: What is joined or formed with heat is designed with symmetry, minimum input and margin to correct
applies_when: When a part is joined, melted or formed with a process that adds heat or deforms the material
not_when: The part is machined or ground completely afterwards and the datums are made there
recognize: A part joined on one side only, with large joints or far from the neutral axis, and fine datums with no later operation
typical_error: Joining on one side only with excess material and expecting the part to come out flat and to size; it warps and there is no material to correct
examples:
- {sector: welded-structures, text: "The weld beads are balanced around the neutral axis, with the minimum size that meets the strength; the reference faces are left with stock for machining.", validated: false}
- {sector: fixtures, text: "The welded body of a fixture is stress-relieved or normalized and its locating seats are machined after welding.", validated: false}
- {sector: sheet-metal, text: "A welded cabinet uses intermittent, alternating beads instead of a continuous bead on one side only.", validated: false}
- {sector: injection-molds, text: "Uniform cooling and even walls reduce the warpage of the plastic part.", validated: false}
sources:
- "TWI, Distortion prevention by design: weld near the neutral axis, minimum weld section, balance, intermittent welding; poor fit-up and overwelding increase distortion (read)."
- "Applied Engineering, Jig and Fixture Design (summary of Hendriksen, 1973): annealing or normalizing after welding (read)."
- "MIT 2.70 (Slocum), Fundamental principles, 2020: symmetry can help or hurt (read)."
when:
  roles: [soldado]
  stages: [layout]
---
