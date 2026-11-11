---
id: design-clamping-force-toward-supports
type: principle
title: The clamping force points toward the supports that locate, not into the air, and does not deform the part
applies_when: When a part is held in position with a clamp, a nut, a strap or a screw
not_when: The part is held by gravity or by shape, with no clamping force
recognize: A clamp whose force line does not pass through a support, or that loads a thin area with no support underneath
typical_error: Tightening against empty space; the part flexes, lifts off the support and the location is lost
examples:
- {sector: fixtures, text: "The clamping jaw sits right above the locator; an equalizer distributes the force between two points.", validated: false}
- {sector: power-transmission, text: "The locknut of a bearing tightens against the shaft shoulder, not against a free area.", validated: false}
- {sector: welded-structures, text: "A bolted joint tightens on a bearing plate and not on a thin flange.", validated: false}
- {sector: sheet-metal, text: "The clamp on a thin sheet has support underneath so that it does not flex.", validated: false}
sources:
- "Applied Engineering, Jig and Fixture Design (summary of Hendriksen, 1973): equalizers align the clamping force with the locators and distribute it so as not to deform thin parts (read)."
when:
  roles: [fijacion]
  stages: [layout, detail]
  ops: [mates, joints]
---
