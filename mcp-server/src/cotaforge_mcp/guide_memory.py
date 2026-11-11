"""``cad_guide(topic="memory")``: how memory is used and saved, and the roles of bodies and components."""

from cotaforge.memory import OPS_VOCABULARY, SECTORS_FILENAME, base_sector_table

from cotaforge_mcp.design_memory import DEFAULT_ROLE_COLORS, ROLES_FILE

SECTOR_IDS = ", ".join(base_sector_table().ids)

MEMORY_TEXT = f"""Memory is the engineer's knowledge. Knowing an entry is not applying it: use it at the decision moment.
USING IT
1. An op result may end with `memory: <id> - recognize: <situation>`: that entry's triggers (an op, a role in the model,
   a stage) matched. `memory(action=get, id)` reads it: its recognise-in-the-model text, examples and the typical error.
   A principle also lists its related practices (one line each, the project's sector first): `get` the one that fits.
2. At each milestone (end of concept, layout, detail) call `memory(action=review, stage)`. It refuses and lists every
   triggered entry without an answer. For each: `get` it (an answer without reading it is refused), look at the part it
   is about, and `memory(action=answer, id, text="applies: how, in which part" | "does not apply: why")`. The answers
   are saved with the document (doc save) and the engineer reads them at the gate.
3. The project's sector, processes and language: `memory(action=brief, sector, processes, language=es|en)`; ask
   the engineer, once (his language is set at install: change it only if he asks). Write what he
   says ("chapa y soldadura" is fine): it is resolved to canonical sector ids ({SECTOR_IDS}); several sectors are
   allowed, the first one weighs most. Examples of the project's sector come first, as the rule; at most two of other
   sectors follow, marked as analogies (ask whether they apply here). Undeclared, `general` or an unknown sector: all
   examples are analogies from up to three sectors. An ambiguous word ("matrices") is never assigned: ask which one.
   A practice of another declared sector is neither pushed nor required. `memory(action=index)` lists ids
   only, grouped by scope, type and sector (`search` shows titles and when each applies); the base practices of
   the project's sector are listed, the others are one line per sector, and `index, sector=<id>` lists one.
SAVING IT (the engineer governs what becomes memory)
- Save only what he states or corrects. `save` writes status=proposed: listed as pending, never pushed, never required.
  He approves it with `memory(action=approve, id, by, text)`: you call that ONLY on his explicit instruction, quoting it.
  process and material entries (his declarations) stay active as before.
- ONE idea per entry. A `principle` is general: no sector or process words, no brand or part numbers, no measured
  numbers (entry ids such as `peine-5mm` are names, not measures). Those belong in a `practice` (with `sector` and
  tags), linked both ways with `related`. A principle needs `recognize` (the situation as seen in the model, not a
  category) and at least one example. A refused save says what to split.
- Before writing, `save` shows similar entries: update one (save with its id) or confirm a new one (`new=true`).
- Scope: project by default. A `user` save (every future design) stays proposed whatever the provenance: ask ONE
  question, "¿Esto vale para tus próximos diseños o solo para este proyecto?", then `approve ... scope=user|project`
  (project moves it).
- A fact about ONE design (its lifting, mass, where something goes, a client's choice) is not a rule: it goes to the
  project (a `case` entry or assumptions-and-decisions.md). Principles and practices: only what applies to future designs.
ENTRY FIELDS (all optional)
- recognize: the situation as seen in the model. examples: [{{sector, text, validated}}] (validated=true only if he
  confirmed it; sector is a canonical id). typical_error. cases: ids of `case` entries (type case: case {{paths, facts,
  teaches}}: a real design).
- related: [ids] of the entries it is linked with (a principle and its practices); an id that does not exist is only a
  warning. replaces: <base id>: his entry replaces a base one, which is then neither pushed nor required (the index
  says by which entry).
- sources: [text] on principles and practices: the standard, book or manufacturer guide. A practice with a measure and
  neither `sources` nor `provenance` saves with a warning. provenance {{source, reference, by, date}}; source: shop_measurement, supplier_datasheet,
  standard, user_statement, correction, reference_book, manufacturer_guide.
- sector: the sector of a practice. when: {{roles: [...], stages: [concept|layout|detail], ops: [...]}}: all given
  fields must match. ops vocabulary: {", ".join(sorted(OPS_VOCABULARY))}.
- The engineer adds sectors or aliases in {SECTORS_FILENAME} (memory/ of the project or of the user):
  sectors: [{{id, name, aliases}}].
ROLES (of bodies and components; stored in the document)
- Set on the op that makes the body or component: `op(name=..., params={{..., "role": "soldado"}})`, or later
  `op(edit=<step>, params={{"role": "soldado"}})` ('' clears it). Roles: {", ".join(DEFAULT_ROLE_COLORS)}; the engineer
  adds roles and changes colours in {ROLES_FILE} of the design space (roles: {{name: "#rrggbb"}}).
- A role colours the body in the window; `view(action=style, color="roles")` paints all of them again.
- Roles trigger entries (when.roles) and are what tells the review which situations exist in the model.
SEVERAL CAD SOFTWARE
The memory is shared by every one. `cad="this"` in the entry marks it for the one you run; use it only for
  that software's own tools or limits, never for design practice, flows, tolerances or the engineer's preferences. An
  entry of another software is not listed or pushed here; `index, text=all_cad` shows what is hidden.
"""
