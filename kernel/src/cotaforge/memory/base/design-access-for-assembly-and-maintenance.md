---
id: design-access-for-assembly-and-maintenance
type: principle
title: Access for the hand, the tool and the instrument is checked at every joint and every adjustment
applies_when: When there are screws, joints, adjustments, lubrication points or areas that are inspected or cleaned
not_when: An internal joint that is not reopened and is verified before closing
recognize: A screw, joint or adjustment surrounded by walls, with no space or entry direction for the tool
typical_error: Placing the element where the model allows it but the tool or the hand do not fit; assembly requires dismantling half the equipment
examples:
- {sector: welded-structures, text: "A joint in a closed section gets an access hole for the torch and for depositing the weld bead.", validated: false}
- {sector: special-machines, text: "The adjustments of a sensor are reachable with the guard in place, without removing the guard.", validated: false}
- {sector: fixtures, text: "The fixture leaves room for the fingers and the tool when loading and unloading the part.", validated: false}
- {sector: hygienic-equipment, text: "Product-contact surfaces are accessible for inspection and cleaning without dismantling.", validated: false}
sources:
- "Applied Engineering, Jig and Fixture Design (summary of Hendriksen, 1973): minimum clearance for the fingers and accessibility for cleaning and use (read)."
- "EHEDG Doc 8 (2004), section 6.2: surfaces accessible for visual inspection and manual cleaning (read)."
- "AISC Steelwise (2013), weld access hole: only a search snippet, article not opened."
when:
  stages: [layout, detail]
  ops: [check_interference]
---
