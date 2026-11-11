---
id: sheet-metal-min-inner-bend-radius
type: practice
title: Inner bend radius not smaller than the thickness; the same radius on all flanges
sector: sheet-metal
applies_when: When defining the inner radius of a bend
not_when: The manufacturer has already published its table for that material and thickness
recognize: A radius smaller than the thickness, or different radii on flanges of the same part
typical_error: A radius too small for the material, which cracks; or a different radius per flange, which calls for more setups and costs more
related:
- design-tool-reaches-the-feature
sources:
- "Hubs, Sheet metal fabrication design guide (read)."
- "Xometry, minimum bend radius table (read); it does not distinguish alloys."
- "Protolabs, The basics of bend radii in sheet metal (read)."
when:
  stages: [detail]
  ops: [base_flange]
---
- Hubs: inner radius equal to or greater than the thickness.
- Xometry (multiples of the thickness T, by thickness range 1 to 6 mm, 6 to 12 mm and 12 to 25 mm): aluminum 1 T, 1.5 T and 2 to 3 T; steel 0.8 T, 1.2 T and 1.5 to 2.5 T; stainless steel 2 T, 2.5 T and 3 to 4 T. It is a starting point: confirm with the material supplier.
- Protolabs: a radius of 0.030 in (0.762 mm) works in 95 % of parts; call out the same value on all flanges so as not to add cost with extra setups; 6061-T6 may need a larger one. Its standard tooling covers 0.010 in to 0.250 in.
- These are guidelines from specific manufacturers, not a standard: confirm with the shop that will make the part. The grain direction relative to the bend is not quantified in any of these sources.
