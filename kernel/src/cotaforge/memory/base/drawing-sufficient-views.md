---
id: drawing-sufficient-views
type: principle
title: A drawing carries the necessary views and none more
applies_when: When creating or reviewing the views of a drawing sheet
not_when: A presentation view or an isometric that the engineer expressly asked for
recognize: The sheet repeats a view that adds no dimension, or the view where a detail to be manufactured is visible is missing
examples:
- sector: fixtures
  text: 'A plate with holes on one face only: two views are enough; the third repeats the outline.'
  validated: false
- sector: sheet-metal
  text: 'A bend with holes in the tab: the flat view and a profile of the bend; not all six views.'
  validated: false
- sector: power-transmission
  text: 'A shaft with a key: side view plus a section of the key; the front view is superfluous.'
  validated: false
typical_error: Putting in all the views out of habit and leaving unshown the face where the holes or the operations are
when:
  stages:
  - detail
  ops:
  - drawing_create
---
Ask yourself, view by view: which dimension or which manufacturing detail is visible only here? If none, it is superfluous.
