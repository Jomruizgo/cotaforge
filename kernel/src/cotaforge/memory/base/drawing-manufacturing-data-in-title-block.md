---
id: drawing-manufacturing-data-in-title-block
type: principle
title: 'The data block says what the outline cannot: material, finish, treatment and general tolerance'
applies_when: Before delivering or exporting a sheet that is to be manufactured
not_when: A presentation sheet that is not manufactured
recognize: Material, finish, treatment or general tolerance that matter and do not appear on the sheet
examples:
- sector: welded-structures
  text: Joint and edge preparation note in the notes block, and the general tolerance of the assembly.
  validated: false
- sector: precision-machining
  text: Heat treatment, hardness and finish of the seats on the sheet.
  validated: false
- sector: sheet-metal
  text: Material, thickness, burrs and surface finish in the data block.
  validated: false
typical_error: Delivering the sheet without material or finish and assuming the shop knows which it was
sources:
- 'The general tolerance is invoked with a note (ISO 2768; the geometric part is replaced by ISO 22081:2021). Catalog listings; standards not opened.'
- 'Title block structure (ISO 7200): standard not opened.'
when:
  stages:
  - detail
  ops:
  - drawing_export
---
