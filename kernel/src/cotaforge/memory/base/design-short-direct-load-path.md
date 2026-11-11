---
id: design-short-direct-load-path
type: principle
title: The force travels a short, direct path, with the least possible bending
applies_when: When a part or assembly transmits a load between two points, or must hold a position under load
not_when: A structure with no load or stiffness requirement
recognize: The load passes through a long cantilever, through several parts in series or through a detour instead of going straight to the base
typical_error: Loading in bending what could work in tension or compression; the result is more material, more deflection and more play
examples:
- {sector: welded-structures, text: "A long cantilevered bracket is triangulated or shortened so that the load goes straight down to the base.", validated: false}
- {sector: special-machines, text: "An open C-frame is closed into a portal or stiffened where the loop between tool and part is long.", validated: false}
- {sector: fixtures, text: "The clamping force goes down through the locator to the base without passing through a thin plate.", validated: false}
- {sector: injection-molds, text: "The ribs are aligned with the load direction instead of thickening the whole wall.", validated: false}
sources:
- "MIT 2.70 (Slocum), Fundamental principles, 2020: structural loop; a long, open loop gives less stiffness and less accuracy (read)."
- "Hubs, injection molding guide: ribs (read)."
- "Pahl and Beitz, Engineering Design: short, direct force path; book not opened, only a secondary summary."
when:
  roles: [estructura, soporte]
  stages: [layout]
  ops: [boolean_union, mates, joints, insert_component, place_component]
---
