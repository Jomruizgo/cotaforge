---
id: drawing-dimensions-from-functional-datums
type: principle
title: Dimensions start from the datums the function uses and are read without calculating
applies_when: When choosing where each dimension of a part runs from and to, relative to other parts
not_when: A part with a single datum face and no critical relationship
recognize: A dimension whose value has to be obtained by adding or subtracting others, or by measuring to a point that does not physically exist
examples:
- sector: fixtures
  text: The locator dimensions start from the faces that locate the fixture on the machine.
  validated: false
- sector: sheet-metal
  text: A bend is dimensioned to its outer face, which is measured with a caliper, and not to the bend axis.
  validated: false
- sector: precision-machining
  text: The lengths of a stepped shaft start from the shoulder that rests on the bearing, not from the end.
  validated: false
typical_error: Dimensioning from what is convenient to draw; the shop has to calculate, and the accumulation between datums exceeds the tolerance
related:
- drawing-one-dimension-per-feature-direction
sources:
- 'ISO 129:1985 (scan seen in summary): dimensions on the view or section that shows the feature most clearly.'
- 'ISO 5459:2024 (catalog listing): datums and datum systems. Some shops dimension to the virtual intersection by convention: confirm with the shop.'
when:
  stages:
  - detail
  ops:
  - drawing_dimension
---
