# How it works

## Plan first

`/cotaforge:plan` starts a design. The agent asks for the sector and the process that makes the part. The list of steps is always readable: ask for the operation tree.

## Three ways to start

`/cotaforge:plan` asks once how you want to work, and remembers your answer.

- **Sketch first.** The agent researches, proposes a quick sketch in FreeCAD, agrees it with you, saves a copy, and only then builds the detail.
- **Direct.** For known architectures: almost straight to the final design.
- **Systematic design**: the classic method of design textbooks. Functions and alternatives first, then the concept and the detail.

We recommend the sketch first. You see the concept early and can change course before spending more tokens on detail.

## Small steps

One operation per call: a sketch, an extrusion, a hole. Each returns measured volume, size and validity. Every sketch is fully dimensioned, so changing a number changes the part.

## It questions you

If a dimension, datum or decision looks wrong, the agent says so first, with the reason. If you insist, it does it and writes the decision in `assumptions-and-decisions.md`.

Before it models a standard part, it asks whether you can buy it.

## Memory

Memory is small text files, one idea each.

- **Project memory:** the `memory` folder of your project.
- **User memory:** `.cotaforge` in your user folder. It applies to all your designs.

The agent proposes and you approve. It asks whether a rule is for all your designs or only this project. You can edit the files by hand.

> `/cotaforge:fix` On a threaded hole, the chamfer is made after tapping, never before. It applies to all my designs.

## Hole colours

| Colour | Meaning |
|---|---|
| Blue | Threaded hole. |
| Red | Precision hole (fit H6, H7 or H8). |
| Green | Plain hole. |

## Drawings

1. The designer (the agent you talk to) finishes the part.
2. The drafter makes the sheet in its own window and asks what it does not know.
3. A reviewer reads the sheet: you, the fabricator agent, or both.
4. Every point is fixed or answered. Then the PDF is written.

## Commands

| Command | What it does |
|---|---|
| `/cotaforge:plan <problem>` | Starts a design. |
| `/cotaforge:fix <text>` | Saves a correction in memory, after you approve it. |
| `/cotaforge:declare <text>` | Declares the allowed materials or processes. |
| `/cotaforge:resume <file>` | Resumes a FreeCAD file and says its state. |
| `/cotaforge:review <focus>` | Asks an independent reviewer to measure the design. |
| `/cotaforge:friction <text>` | Writes a note in `friction.md` of your project. |

Everything else: [manual](manual.md).
