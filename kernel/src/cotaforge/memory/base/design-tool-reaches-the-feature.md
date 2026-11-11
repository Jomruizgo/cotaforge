---
id: design-tool-reaches-the-feature
type: principle
title: Each feature is made with a real tool that reaches it, with the clearance and radius that tool leaves
applies_when: When there are inside corners, cavities, short protrusions, deep holes or features behind others
not_when: The feature is made with a process with no contact tool whose minimum limit is already known
recognize: Sharp inside corner, deep and narrow cavity, very short protrusion or a feature with no entry angle
typical_error: Drawing the feature as the model allows and not as the tool allows; the shop rounds it, redoes it or charges more
examples:
- {sector: precision-machining, text: "The radius of a vertical inside corner is specified larger than that of the end mill that reaches the depth.", validated: false}
- {sector: sheet-metal, text: "A very short flange does not fit in the bending die: it is lengthened or cut afterwards.", validated: false}
- {sector: injection-molds, text: "A feature with an undercut gets a slide or is redesigned; without draft it does not come out of the cavity.", validated: false}
- {sector: welded-structures, text: "The torch must be able to reach the weld at its working angle.", validated: false}
sources:
- "Hubs, design guide for CNC machining; Protolabs, machining DFM toolkit (read)."
- "Protolabs and Hubs, sheet metal guides (read); Hubs, injection molding guide (read)."
- "Welding torch access (angle and clearance): no open source, unverified."
when:
  stages: [detail]
  ops: [draft, edge_flange, shell]
---
