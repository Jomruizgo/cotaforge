---
name: friction
description: Record at once that something got in the way or failed while using the harness, to improve it later. Use it when the engineer says something got in his way, or invokes this command.
---

The engineer wants to log a friction (`$ARGUMENTS`). Record the fact and move on; do not discuss it or try to fix it now. Speak to the engineer in the project language (`language` in `memory(action=brief)`; Spanish if none is set).

1. Append an entry to the end of `friction.md` in the design space folder (create it if missing; keep writing to `friccion.md` if that is the file the project has). One block per entry, written in the project language:
   - Date and time.
   - What the engineer said, in his words.
   - What you were doing: the last tool used and a short summary of its result.
2. Do not delete or rewrite earlier entries.
3. Reply with one line confirming it was recorded and carry on with what you were doing.
