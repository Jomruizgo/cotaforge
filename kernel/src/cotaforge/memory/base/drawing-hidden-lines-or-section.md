---
id: drawing-hidden-lines-or-section
type: principle
title: 'Hidden lines or section: choose what reads best'
applies_when: When there is internal or covered geometry that must be manufactured or verified
not_when: Geometry visible from the outside in some view
recognize: A view full of crossing hidden lines, or a blind hole or a void that is not visible in any view
examples:
- sector: process-and-transport-equipment
  text: 'A housing with cavities: a longitudinal section instead of hidden lines.'
  validated: false
- sector: power-transmission
  text: 'A bearing housing: a partial section through the axis.'
  validated: false
- sector: fixtures
  text: 'A plate with a blind pocket: a single hidden line is enough if it is simple; if there are several, a section.'
  validated: false
typical_error: Leaving five overlapping hidden lines instead of a section that shows the interior just once
when:
  stages:
  - detail
  ops:
  - drawing_create
---
