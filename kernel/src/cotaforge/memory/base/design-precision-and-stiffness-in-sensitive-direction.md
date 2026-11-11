---
id: design-precision-and-stiffness-in-sensitive-direction
type: principle
title: Precision and stiffness are concentrated in the direction where an error matters
applies_when: When deciding where to spend precision, tolerance or stiffness in a part or a mechanism
not_when: All directions affect the function equally
recognize: The same precision is requested in all directions, or it has not been stated in which direction a position error harms the function
typical_error: Spreading the precision effort evenly and ending up short in the critical direction; or spending in directions where the error does not matter
examples:
- {sector: precision-machining, text: "In a turned part, the radius matters more than the axial position of a face: the tolerance goes where the function acts.", validated: false}
- {sector: fixtures, text: "The locators are adjusted in the direction of the part's critical tolerance and left loose in the others.", validated: false}
- {sector: special-machines, text: "In a manipulator, repeatability is demanded in the insertion direction and relaxed in the approach plane.", validated: false}
sources:
- "MIT 2.70 (Slocum), Fundamental principles, 2020: sensitive directions; put the accuracy effort in the directions where it is needed (read)."
---
