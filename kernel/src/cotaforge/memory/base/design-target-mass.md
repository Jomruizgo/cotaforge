---
id: design-target-mass
type: principle
title: Mass has a target and is compared with what is measured
applies_when: When the design has a mass limit for handling, transport or load
not_when: There is no mass or handling limit
recognize: An assembly whose mass nobody has compared with the target, or a target that was not stated
examples:
- sector: fixtures
  text: 'The fixture is carried by a person: a mass target is set and measured on the model.'
  validated: false
- sector: process-and-transport-equipment
  text: 'A module that is lifted: the measured mass is compared with the capacity of the lifting means.'
  validated: false
- sector: special-machines
  text: 'An arm on an actuator: the mass drives the actuator and is measured before sizing it.'
  validated: false
typical_error: Reaching the detail without knowing whether the mass fits the target
when:
  stages:
  - layout
---
Ask the engineer for the target if it was not given; the mass comes from the measuring tool, never from an estimate.
