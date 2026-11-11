---
name: fix
description: Save an engineer's correction in memory so it loads in future sessions. Use it when the engineer corrects something you did or teaches you a rule you should have known.
---

The engineer corrected you or taught you something (`$ARGUMENTS`). Save it as a memory entry of type `correction`. Speak to the engineer in the project language (`language` in `memory(action=brief)`; Spanish if none is set); the entry text is his, in his words.

1. First check `memory` with `action="search"` in case a similar entry exists; if so, update it (same `id`) instead of duplicating it. `save` also shows the similar ones and writes nothing until you choose to update or confirm a new one (`new=true`).
2. Classify before writing, ONE idea per entry: a general rule (holds in any sector) is a `principle` and carries no sector words, brands, part codes or measures with units; it needs `recognize` (the situation as seen in the model) and at least one example `{sector, text, validated}`. What belongs to one sector or process (figures, names, how it is done there) is a `practice` with `sector` and `tags`, linked from the principle. A one-off correction is a `correction`; a real design with its files and measures is a `case`. If what he said mixes both, make two entries.
3. Write from what the engineer said, not your interpretation: `id` (short slug), `type`, `title`, `applies_when` and `not_when` (when it does NOT apply; mandatory). If he did not say when it does not apply, ask him (a single question) instead of inventing it. If he says when it should appear (a role, a stage, an operation) put it in `when`.
4. If it carries a number it needs `provenance` (`source="correction"`, who said it and the date). Never save a number you estimated.
5. The scope is the project. A fact about ONE design (how it is lifted, its mass, where something goes, what a client chose) is not a rule: it goes to the project (a `case` or `assumptions-and-decisions.md`). `scope="user"` only for what holds in future designs; the server leaves it `proposed` even if you quote his words in `provenance`, and tells you to ask him ONE question (in his language): does this hold for his next designs or only for this project?
6. Memory is shared by all CAD software. Mark the entry `cad="this"` only if it is really about the tools or limits of THIS CAD software (how to use its native macro, a limit of its own). Design practice, flows, chamfers, tolerances and the engineer's preferences are not marked: they hold for all. A marked entry is not loaded in another CAD software.
7. Save with `memory` and `action="save"`. The entry stays `proposed`: pending, neither pushed nor required. Show the engineer what the server returns (the full entry and its classification). Only if he orders it approved, call `action="approve"` with `by` and his words in `text`; if it was in `user`, add `scope="user"` (his next designs) or `scope="project"` (only this project: it moves to the project), according to his answer.
8. Confirm in one line: the saved `id` and that it awaits his approval.

Save only what the engineer stated or corrected. If something is unclear, ask before saving.
