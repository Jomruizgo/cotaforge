---
id: drawing-one-dimension-per-feature-direction
type: principle
title: Each feature is dimensioned only once per direction, with no closed chains
applies_when: When deciding which dimensions each view of the sheet carries
not_when: Auxiliary dimensions in parentheses placed on purpose as information
recognize: A closed dimension chain with the overall dimension repeated, or two dimensions that fix the same direction of the same feature
examples:
- sector: fixtures
  text: Holes are dimensioned from two reference edges and not chained from one to another with the overall dimension as well.
  validated: false
- sector: welded-structures
  text: A tube is dimensioned by its overall length and not by the sum of its segments.
  validated: false
- sector: sheet-metal
  text: A bent foot carries the overall outer dimension and that of the flange, not all three with the thickness.
  validated: false
typical_error: Closing the chain; one dimension is redundant, may contradict the others and the shop does not know which governs
related:
- drawing-dimensions-from-functional-datums
sources:
- 'ISO 129:1985 (scan seen in summary): no more dimensions than necessary and no feature defined by more than one dimension in the same direction. ISO 129-1:2018 keeps the presentation of dimensions; normative text not opened.'
when:
  stages:
  - detail
  ops:
  - drawing_dimension
---
