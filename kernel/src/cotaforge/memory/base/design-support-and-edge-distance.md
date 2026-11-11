---
id: design-support-and-edge-distance
type: principle
title: Every support and every hole leaves enough material up to the edge
applies_when: When there are holes, supports or joints near an edge
not_when: An edge that is reinforced or finished on purpose
recognize: A hole or a support close to an edge, with a wall that nobody has compared with what the manufacturing process requires
examples:
- sector: sheet-metal
  text: The distance from the hole to the edge comes from the process guideline and is measured in thicknesses; it is not a proportion of the diameter.
  validated: false
- sector: welded-structures
  text: A base plate with the anchor bolts far from the edge of the concrete and of the plate.
  validated: false
- sector: fixtures
  text: The hole of a locating pin with enough wall around it.
  validated: false
typical_error: Placing the hole at the edge to save material and leaving a wall that breaks or deforms
related:
- sheet-metal-hole-edge-and-spacing
when:
  roles:
  - soporte
  - apoyo
  - fijacion
  stages:
  - detail
  ops:
  - hole
  - sketch
---
The figures depend on the process and the thickness: they are in the linked practices, not here.
