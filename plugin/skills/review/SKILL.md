---
name: review
description: Ask the reviewer agent for an independent review of the current design; it measures, checks what was declared and separates what was verified from judgement. Use it before calling a part finished or exporting it.
---

Ask for a review of the open design (`$ARGUMENTS` gives the focus, if any). Speak to the engineer in the project language (`language` in `memory(action=brief)`; Spanish if none is set).

1. Launch the `reviewer` agent with a concrete task: which part to review, the requested focus and the constraints the engineer declared. Do not hand it your conclusions: it must measure on its own.
2. When it answers, give the engineer the report as it is, without softening it, in two groups: what was verified with tools and what is judgement only.
3. If there are violations, propose the smallest next step that resolves them and wait for the engineer's decision before touching the model.
