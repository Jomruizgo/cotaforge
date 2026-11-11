---
id: transmission-bearing-seat-only-over-its-width
type: practice
title: The shaft fit goes only on the bearing seat; the shaft is stepped and the shoulder locates it
sector: power-transmission
applies_when: When modeling a shaft that carries bearings and when tolerancing the diameters of its seats
not_when: A bearing on an adapter or withdrawal sleeve on a plain shaft; a needle bearing that runs directly on a hardened and ground shaft made for it; a shaft that by its function is a ground guide over its whole length
recognize: A bearing on a shaft section of its same diameter much longer than the bearing (for example, two bearings on a plain shaft of a single diameter)
typical_error: Two bearings on a shaft of a single diameter; each is pushed along a long fit to its place and nothing locates it
related:
- transmission-bearing-fit-by-load
- transmission-shaft-two-supports-fixed-and-floating
- design-access-for-assembly-and-maintenance
sources:
- "Budynas and Nisbett, Shigley's Mechanical Engineering Design, 8th ed., McGraw-Hill, 2006, ch. 7, section 7-3 \"Shaft Layout\", pp. 349-353 and 360 (read): the shaft is in general a stepped cylinder; the shoulder is the main means of locating components; for assembly, the largest diameter goes in the center and progressively smaller diameters toward the ends, to slide components on from the ends; the shoulders for bearings and gears follow the catalog recommendation (D/d between 1.2 and 1.5 as a first approximation); the bearing that carries no axial load may be press-fitted without a shoulder (figs. 7-3 and 7-4). The text does not say in these words that the fit goes only on the seat: it is the consequence of stepping the shaft."
- "NSK Americas, Technical Insight \"Design for Shafts and Housings\", rev. 2 (read): the shaft shoulder must be high enough to support the face of the ring; the shaft fillet must be smaller than the corner radius of the ring; the ring must stand proud of the shoulder so that a puller can be used."
---
- The bearing fit (the tolerance zone of the seat) is declared only over the width of the ring plus its chamfer or its undercut. The rest of the shaft is of smaller diameter and carries no fit.
- The shaft is stepped and each bearing is mounted from the end of the smaller diameters: the assembly travel of each bearing crosses only diameters smaller than its seat. A bearing is never pushed along a long section with a fit (it damages the seat and the ring, and needs a force that the assembly does not have).
- The step is the shoulder that locates the bearing. Its diameter and the maximum fillet radius come from the abutment dimensions in the bearing catalog, not from an estimate: they are requested from the catalog (`catalog`). The shaft fillet is smaller than the corner radius of the ring, and the ring stands proud of the shoulder so that it can be pulled off with a puller.
- The floating bearing also gets a seat of just the right width. It may go without a shoulder (the fit locates it), but the shaft is still stepped so that it reaches its place from the end without crossing a long fit.
- Server warning: when it measures that a bearing (role `comercial` or bearing name, with a bore) sits on a shaft section of its same diameter longer than 2 times its width, it returns a `memoria:` line with the measurements. It is a warning, it blocks nothing. It does not see a bearing drawn as a disc with no bore, nor a seat that is not cylindrical, nor whether the bearing sits on a sleeve.
