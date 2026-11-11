---
id: design-side-load-off-the-actuator
type: principle
title: An element that pushes or pulls takes force only in its working direction; a guide takes the side load and the misalignment
applies_when: When an actuator, a lead screw or a rod moves a load that travels guided by another part
not_when: The load moves without its own guide and the actuator is sized for the side load
recognize: An actuator, lead screw or rod rigidly connected to a load that moves along another guide
typical_error: Connecting it rigidly and trusting the alignment; the side load wears the seal, the bearing and the rod until it fails
examples:
- {sector: special-machines, text: "The cylinder pushes through a floating joint and a linear guide takes the side load.", validated: false}
- {sector: fixtures, text: "The clamping cylinder of a fixture is mounted so that the side force does not fall on the rod.", validated: false}
- {sector: power-transmission, text: "The nut of a lead screw is floating or has a flexible coupling to absorb the misalignment.", validated: false}
sources:
- "Fluid Power World, Hydraulic cylinder design: preventing side loading: rod-end spherical bearing, rod support and trunnion mounting compensate for the side load; correct alignment is critical (read; popular-science article)."
- "MIT 2.70 (Slocum), Fundamental principles, 2020: Saint-Venant and stability (read)."
when:
  roles: [actuador]
  stages: [layout, detail]
  ops: [mates, joints]
---
