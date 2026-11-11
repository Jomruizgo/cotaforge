---
id: design-outlet-for-what-accumulates
type: principle
title: Every cavity or surface where residue can remain has an outlet or a slope
applies_when: When there are horizontal surfaces, pockets or cavities that can retain liquid, chips, dust or product
not_when: A closed cavity sealed on purpose (vented if there is heat or pressure)
recognize: Horizontal surface, pocket or closed corner that retains liquid or residue
typical_error: Leaving a flat bottom or a pocket with no outlet; residue accumulates, corrodes, contaminates or prevents the part from seating
examples:
- {sector: hygienic-equipment, text: "The surfaces slope toward a drain point and there are no flat areas where liquid stays.", validated: false}
- {sector: fixtures, text: "Under the locators there is an outlet for chips and coolant; no pockets form where they accumulate.", validated: false}
- {sector: welded-structures, text: "Sections and plates that could retain water get drainage.", validated: false}
- {sector: special-machines, text: "The tray under a lubricated mechanism has a slope and a drain.", validated: false}
sources:
- "EHEDG Doc 8 (2004), section 6.4: avoid horizontal, self-draining surfaces (read)."
- "Applied Engineering, Jig and Fixture Design (summary of Hendriksen, 1973): relief around pins and buttons for chips and dirt (read)."
when:
  stages: [layout, detail]
  ops: [shell]
---
