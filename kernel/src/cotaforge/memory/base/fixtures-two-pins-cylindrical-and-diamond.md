---
id: fixtures-two-pins-cylindrical-and-diamond
type: practice
title: Locate by two holes with one cylindrical pin and one diamond pin, with a guided entry
sector: fixtures
applies_when: When locating a part by two holes or by a hole and a contour
not_when: The part is located by a single hole and a face
recognize: Two round pins of the same size on a part with a tolerance on the distance between holes
typical_error: Two cylindrical pins that do not enter because of the tolerance between centers, or that force the part
related:
- design-one-support-per-degree-of-freedom
- design-assembly-only-one-way
- design-how-each-part-is-located
sources:
- "Applied Engineering, Jig and Fixture Design (summary of E. K. Hendriksen, Jig and Fixture Design Manual, 1973): radial location with two cylinders and circular locators (read)."
when:
  roles: [piloto]
  stages: [detail]
  ops: [hole, place_component]
---
- Radial location with two cylinders: a diamond pin or an expanding pin makes it easier to use. Pre-entry with a long lead or a chamfer; successive entry (pins of different lengths to enter one at a time).
- Large circular locators: a triangular relief gives sufficient bearing area with 3 zones of 30° spaced 120° apart, and reduces binding when loading and unloading.
- Split-sleeve expanding pins are over-tightened and damaged: use them with care.
