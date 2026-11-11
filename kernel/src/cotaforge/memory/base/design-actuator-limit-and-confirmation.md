---
id: design-actuator-limit-and-confirmation
type: principle
title: For each actuator it is known what limits its travel and what confirms its position
applies_when: When there is an actuator or a moving part
not_when: A manual movement with no position-control requirement
recognize: An actuator with free travel at both ends, or an end position with no sensor or stop
examples:
- sector: special-machines
  text: 'A pneumatic cylinder: mechanical stop at one end and position sensor at the other.'
  validated: false
- sector: fixtures
  text: 'A clamp: stop that limits the closing and sensor that confirms a part is present.'
  validated: false
- sector: process-and-transport-equipment
  text: 'A gate: limit switches at both ends and a mechanical safety stop.'
  validated: false
typical_error: Relying on the actuator's internal end of stroke as the only limit or taking the position for granted
when:
  roles:
  - actuador
  stages:
  - layout
  - detail
  ops:
  - add_joint
  - set_joint
  - joints
---
