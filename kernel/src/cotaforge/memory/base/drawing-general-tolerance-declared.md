---
id: drawing-general-tolerance-declared
type: principle
title: The general tolerance is declared on the sheet and tightened only where the function requires it
applies_when: When deciding which dimensions carry their own tolerance
not_when: The customer sets tolerances for all dimensions
recognize: A sheet with no general tolerance note, or with a tight tolerance on dimensions that fit with nothing
examples:
- sector: precision-machining
  text: Only the bearing seat and the seal seat carry a fit; the shaft length stays under the declared general tolerance.
  validated: false
- sector: fixtures
  text: The positioning holes carry their own tolerance; the clearance holes, the general one.
  validated: false
- sector: sheet-metal
  text: The general tolerance of the sheet is declared and only the distance between locating holes is tightened.
  validated: false
typical_error: Leaving the general tolerance undeclared (each shop applies its own), or giving a tight tolerance to everything out of caution, or leaving a functional fit without tolerance
sources:
- 'General tolerance by a note on the sheet, for example ISO 2768-mK (linear class m, geometric K). The geometric part, ISO 2768-2, is withdrawn and replaced by ISO 22081:2021 (catalog listings; standards not opened).'
- 'Hubs, design for CNC machining: if no other is indicated, the shop applies its default tolerance, of medium or fine type (read).'
when:
  stages:
  - detail
  ops:
  - drawing_dimension
  - drawing_export
---
