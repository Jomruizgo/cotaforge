---
id: design-escape-directions
type: principle
title: A positioned part cannot escape in any direction unless something prevents it
applies_when: When a part must remain fixed relative to another
not_when: A part that moves freely on purpose
recognize: A supported part that can slide, rotate or lift because only gravity or a single contact holds it
examples:
- sector: fixtures
  text: 'A part in a fixture: three supports, two side stops and a clamp that closes the free direction.'
  validated: false
- sector: special-machines
  text: 'A module on a rail: longitudinal stop, side guide and retention against lifting.'
  validated: false
- sector: process-and-transport-equipment
  text: 'A container on its base: stops in four directions and a vertical anchor.'
  validated: false
typical_error: Constraining three of the six movements and believing the part is fixed
when:
  roles:
  - contacto
  - apoyo
  - piloto
  stages:
  - layout
  - detail
  ops:
  - mates
  - joints
  - insert_component
---
