---
id: design-how-each-part-is-located
type: principle
title: Each part has a defined way of locating itself when joined; screws fasten, dowels or shoulders locate
applies_when: When a part is joined to others and its relative position matters or must be repeatable after disassembly
not_when: A part joined to another by a single contact and adjusted by hand on purpose, or a position that is adjusted at assembly and locked
tags: [locating dowel, locating dowels, dowel pin, registration pin, registro, registros]
recognize: Two parts that touch on one face with no stop, pin or notch fixing their relative position, or joined only with screws in clearance holes
examples:
- sector: welded-structures
  text: A section welded to a plate gets a tab or a stop that locates it without measuring.
  validated: false
- sector: fixtures
  text: A part of the fixture enters a pin fit; it is not located by eye. Two pins on the diagonal and as far apart as possible locate the plate and the screws only fasten it.
  validated: false
- sector: process-and-transport-equipment
  text: A housing is centered with a shoulder before being bolted.
  validated: false
- sector: special-machines
  text: A base plate gets dowels so it can be refitted in the same place after maintenance.
  validated: false
- sector: sheet-metal
  text: Self-locating tabs and slots position the parts before bolting.
  validated: false
- sector: injection-molds
  text: Dowels and guide pillars guide and center the halves; the screws only fasten them.
  validated: false
typical_error: Assuming the operator locates the part by eye or that the screws position it; each assembly turns out different and the position changes on disassembly and reassembly
sources:
- "Applied Engineering, Jig and Fixture Design (summary of Hendriksen, 1973): bodies built up with screws plus dowels for accuracy, two dowels on the diagonal and as far apart as possible (read)."
when:
  roles:
  - soldado
  - piloto
  - calza
  - fijacion
  stages:
  - detail
  ops:
  - insert_component
  - place_component
  - mates
---
