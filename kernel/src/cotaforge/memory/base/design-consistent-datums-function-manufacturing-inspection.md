---
id: design-consistent-datums-function-manufacturing-inspection
type: principle
title: The same datum serves the function, the manufacturing and the inspection
applies_when: When a critical characteristic depends on a datum face, axis or hole to be manufactured, assembled or measured
not_when: A part with no critical relationship to others, of a single operation and with no dimensional verification
recognize: A critical dimension starts from a face different from the one that locates the part in the assembly or on the machine
typical_error: Dimensioning from the datum that is convenient to draw and not from the one the function uses; the shop meets the drawing and the assembly fails because of the sum between datums
examples:
- {sector: fixtures, text: "The hole pattern of the fixture is dimensioned from the same faces that locate the workpiece, not from the edge of the plate.", validated: false}
- {sector: precision-machining, text: "Two bearing seats are referred to their common axis, and that same datum is used for turning and for measuring.", validated: false}
- {sector: sheet-metal, text: "The holes of a bracket are dimensioned from the face that rests on the assembly, even though bending is more convenient from the edge.", validated: false}
- {sector: injection-molds, text: "The critical dimensions of the cavity are referred to the parting plane that is also used to manufacture and measure the insert.", validated: false}
sources:
- "MIT 2.70 (Slocum), Fundamental principles, 2020: sensitive directions and reference elements (read)."
- "ISO 5459:2024 (datums and datum systems): only the standard's catalog listing; standard not opened."
- "ASME Y14.5 (datums): standard not opened, unverified."
---
