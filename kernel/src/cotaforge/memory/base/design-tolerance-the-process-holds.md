---
id: design-tolerance-the-process-holds
type: principle
title: A tolerance that the chosen process cannot hold is not demanded
applies_when: When a dimension or a pattern carries a fine tolerance and the manufacturing process is already known
not_when: The dimension is achieved in a later final operation or is adjusted at assembly
recognize: Fine tolerance on a dimension produced by an imprecise process or by a joint that deforms
typical_error: Dimensioning with a grinding tolerance a part that comes from a coarse process, or setting fine positions on parts that deform when joined; it is rejected or touched up by hand
examples:
- {sector: precision-machining, text: "A seat with a fine tolerance that is only achieved by grinding: the final operation is declared or the tolerance is relaxed.", validated: false}
- {sector: sheet-metal, text: "The fine position of two holes on either side of a bend is not held by the bending: they are cut in the same operation or adjusted at assembly.", validated: false}
- {sector: welded-structures, text: "The fine position of a support on a welded frame is achieved by machining after welding, not with the weld.", validated: false}
- {sector: injection-molds, text: "A narrow tolerance on a long plastic part ignores shrinkage and warpage: it is requested only on the characteristics that justify it.", validated: false}
sources:
- "Protolabs, Design guidelines for sheet metal fabrication: bending tolerances of a specific manufacturer (read)."
- "Hubs, design guide for CNC machining: shop default tolerances ISO 2768 medium or fine (read)."
- "Applied Engineering, Jig and Fixture Design (summary of Hendriksen, 1973): the fixture tolerance is a fraction of the part's (read)."
- "IT grade table by process: teaching sources seen only in summary; no figure is used."
---
