---
id: drawing-hatching-cut-material-only
type: principle
title: The hatching of a section covers only cut material and distinguishes adjacent parts
applies_when: When the sheet has section views
not_when: The sheet has no sections
recognize: Hatching over voids; two adjacent parts with the same direction and pitch; a rib, shaft or screw sectioned lengthwise and hatched
examples:
- sector: special-machines
  text: In an assembly each cut part gets a different hatching direction or pitch.
  validated: false
- sector: sheet-metal
  text: In the section of a thin sheet the profile is filled in black and a thin gap is left between adjacent sections.
  validated: false
- sector: power-transmission
  text: In the longitudinal section of an assembly, shafts, screws and ribs are not hatched; in a cross section they are.
  validated: false
typical_error: Hatching the whole interior of the outline including voids, hatching two parts of an assembly the same way, or hatching elements that are not cut in a longitudinal section
sources:
- 'ISO 128-3 (hatching clause): exists according to its index; the 2022 edition replaces it according to catalog listings. Verify the wording in the current standard.'
- 'BS ISO 128-44:2001, cited in a forum: ribs, fasteners and shafts are not cut in longitudinal sections (weak source).'
when:
  stages:
  - detail
  ops:
  - drawing_create
---
