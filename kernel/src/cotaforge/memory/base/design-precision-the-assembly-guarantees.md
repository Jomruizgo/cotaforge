---
id: design-precision-the-assembly-guarantees
type: principle
title: A precision that the part alone cannot guarantee is not specified
applies_when: When a part carries dimensions or patterns with fine tolerance
not_when: The part is machined complete after joining
recognize: A hole pattern or a dimension with fine tolerance on a part that is joined, bent or cantilevered
examples:
- sector: fixtures
  text: The locating pattern is machined after welding, or the assembly with dowels guarantees it.
  validated: false
- sector: sheet-metal
  text: The locating holes go in a machined part that is joined, not in the bent sheet.
  validated: false
- sector: welded-structures
  text: The fine position of the supports is achieved with an adjustment on site, not with the weld.
  validated: false
typical_error: Dimensioning with fine tolerance a part that deforms when joined or bent, instead of locating at the assembly
when:
  stages:
  - layout
  - detail
  ops:
  - hole
  - unfold
  - edge_flange
  - base_flange
---
