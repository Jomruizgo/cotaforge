---
id: transmission-shaft-two-supports-fixed-and-floating
type: practice
title: A shaft with two supports; one fixes the axial position and the other leaves it free
sector: power-transmission
applies_when: When defining the bearing arrangement of a shaft
not_when: A very long shaft with calculated intermediate supports
recognize: A shaft with both bearings fixed axially
typical_error: Two fixed bearings; thermal expansion or the tolerance of the positions overloads them
related:
- design-one-support-per-degree-of-freedom
- design-free-expansion-at-one-end
sources:
- "MIT 2.70 (Slocum), Fundamental principles, 2020 (read)."
---
- Do not constrain a shaft with more than 2 supports, unless it is very long (Saint-Venant principle). A long shaft constrained axially at both ends can buckle.
- Pairs of angular contact bearings: back-to-back tolerate misalignment poorly, but use axial thermal growth to cancel the radial one (preload and thermal stability at high speed); face-to-face tolerate misalignment, but axial growth adds to the radial and can seize at high speed.
