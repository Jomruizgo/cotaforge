---
id: drawing-thread-designation
type: principle
title: The thread is designated with its pitch and its useful length, not as a plain hole
applies_when: When the part has threaded holes or external threads
not_when: A thread defined by a connection standard that is cited in full
recognize: A threaded hole shown with the plain nominal diameter and no thread designation
examples:
- sector: fixtures
  text: A threaded hole in the plate is dimensioned with its thread designation and its useful depth.
  validated: false
- sector: special-machines
  text: A fastening thread of a guide carries a designation and a minimum engagement length.
  validated: false
- sector: precision-machining
  text: A blind thread states the thread depth and the drill depth.
  validated: false
typical_error: Drawing the thread with the nominal diameter as if it were a plain hole; the shop does not know pitch, class or useful depth
sources:
- 'Thread representation convention (ISO 6410): standard not opened.'
- 'Hubs, design for CNC machining: thread length and engagement (read).'
when:
  stages:
  - detail
  ops:
  - drawing_dimension
  - hole
---
