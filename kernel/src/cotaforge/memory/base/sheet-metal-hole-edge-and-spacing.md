---
id: sheet-metal-hole-edge-and-spacing
type: practice
title: 'Sheet metal: minimum hole size, distance to the edge and between holes'
sector: sheet-metal
applies_when: When placing holes and slots in sheet metal
not_when: The manufacturer uses a different rule for that thickness and tool
recognize: A hole smaller than the thickness, or less than one thickness from an edge
typical_error: Holes that distort, crack or tear when cut or bent
related:
- design-support-and-edge-distance
sources:
- 'Hubs, Sheet metal fabrication design guide: read.'
- 'Protolabs, Design guidelines for sheet metal fabrication: read.'
when:
  ops:
  - hole
  - edge_flange
  - base_flange
  stages:
  - detail
---
- Hole or slot diameter: not smaller than the thickness (Hubs, Protolabs).
- Distance from hole to edge: at least the thickness (Hubs). Protolabs: if the thickness is 0.914 mm (0.036 in) or less, 1.574 mm (0.062 in); if it is greater, at least 3.175 mm (0.125 in).
- Distance between holes: at least 2 times the thickness (Hubs).
- These are guidelines from specific manufacturers, not a standard: confirm with the shop that will make the part.
