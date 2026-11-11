---
id: drawing-feature-size-and-position
type: principle
title: Every feature gets its size and its position
applies_when: When the part has protrusions, slots, pockets or holes
not_when: Features that a standard or an already dimensioned pattern defines completely
recognize: A slot or pocket with its width but no position, or with a position but no depth
examples:
- sector: power-transmission
  text: 'A keyway: width, depth and position from the end.'
  validated: false
- sector: fixtures
  text: 'A pocket for a part: length, width, depth and position relative to the reference edges.'
  validated: false
- sector: sheet-metal
  text: 'A cutout in a tab: size and distance to the bend.'
  validated: false
typical_error: Dimensioning the size of a feature and forgetting where it is, or the other way around
when:
  stages:
  - detail
  ops:
  - drawing_dimension
---
