# One-minute video: script for the owner

Goal: an engineer who has never heard of cotaforge understands in one minute what it does and wants to try it. For the home page of the site (and for the README). Record a real session. Do not show customer files.

## Before recording

- A clean project folder (Start menu > **Create new project (cotaforge)**), FreeCAD window visible, side by side with Claude Code.
- Screen 1920 x 1080, zoom the chat text to be readable at 720p. Close other windows and notifications.
- Rehearse once. The agent's words change, so keep the take that reads best. Cut waiting time (speed up 4x or cut) and say so on screen ("sped up").
- Use a synthetic part: a 100 x 60 x 8 mm plate with four M8 holes. Pre-load one memory rule so the memory shot is quick.

## Shot list (about 60 seconds)

| Time | On screen | What you say |
|---|---|---|
| 0:00 - 0:06 | Title card: **cotaforge**. Then the empty chat and the empty FreeCAD window. | "This is cotaforge. An AI colleague that models in FreeCAD." |
| 0:06 - 0:16 | You type `/cotaforge:plan a 100 x 60 x 8 mm steel plate with four M8 through holes`. The agent asks the process and the sector; you answer in one line. | "I ask for a plate in plain words. It asks me what I would be asked in the shop: how will it be made?" |
| 0:16 - 0:28 | FreeCAD: the plate grows (sketch, extrusion, holes). Zoom on the volume line returned after one step. | "It builds in small steps, and I see each one. The numbers are measured by FreeCAD, never guessed." |
| 0:28 - 0:36 | You ask for a chamfer or a dimension that is wrong (for example a hole 3 mm from the edge). The agent objects with a reason. | "It questions me like a colleague would. If I insist, it does it and writes it down." |
| 0:36 - 0:44 | You type `/cotaforge:fix` with one shop rule. The agent asks "for all your designs or only this project?". You answer. | "I teach it a rule once. It asks me before saving it, and it uses the rule next time." |
| 0:44 - 0:54 | The drafter's window draws the sheet. Then the fabricator's report appears: two points. Show the sheet with the coloured isometric. | "A drafter makes the shop drawing. A second agent reads only the drawing, like the machinist, and tells me what is missing." |
| 0:54 - 1:00 | The PDF opens. End card: the site address and "Windows + FreeCAD + Claude Code. Free, MIT." | "Free and open source. Install it in minutes. The link is below." |

## Tips

- One idea per shot. No shot longer than 12 seconds.
- Do not read the screen aloud. Say why it matters.
- Captions in English on the video, with a Spanish version of the voice-over for the `es` pages.
- Export 1080p, under 20 MB if it goes in the repository; otherwise upload it and link it.
- Use the same take for stills: the hero image of the site comes from 0:16 - 0:28.
