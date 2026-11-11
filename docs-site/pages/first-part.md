# Your first part

A steel plate, 100 x 60 x 8 mm, with four holes. From the first message to the PDF.

You need an open project: see [Install](install.md#your-first-project). The agent's words change from session to session. The steps and the measures do not.

## 1. Ask

> `/cotaforge:plan` a 100 x 60 x 8 mm S235JR plate with four 9 mm through holes, 15 mm from the edges.

The agent asks a few short questions, such as the process that makes the part. Answer in plain words. It writes the answers down.

It also chamfers the plate corners at 45°, by default. Say so if you do not want that.

## 2. Read the plan

The agent says the plan in a few lines before it models. Agree or change it.

## 3. Watch

The FreeCAD window opens. The plate grows step by step: sketch, extrusion, holes. After each step the agent reads the measures FreeCAD returns.

Check one by hand. Each Ø9 hole removes π × 4.5² × 8 ≈ 509 mm³, and each 4 × 45° corner removes 4 × 4 / 2 × 8 = 64 mm³. If the agent's numbers differ, ask why.

Say "stop" to interrupt, or "undo the last step".

## 4. Draw

> Make the shop drawing on A3, top view and isometric. Locate the first hole from the left edge and the front edge, and the others from it by centre distance.

The agent hands the part to the **drafter**, which opens in its own window. It asks what it does not know, such as the datum edges. The result:

![The shop drawing of the plate: top view and isometric. The first hole is located from datums A and B, the others from it by centre distance, and the callout reads 4x Ø9 thru.](assets/img/plate-sheet.png)

*A synthetic plate, made with CotaForge and FreeCAD. Review mode `own`: the agent answered its own review points.*

## 5. Review

The PDF is not written until someone other than the drafter has looked at the sheet. The agent asks you once whether you review it or the **fabricator** agent does. The fabricator reads only the drawing, never the 3D model, and lists what is missing or unclear. If you do not answer in 15 minutes, it reviews.

Every point is fixed or answered. Answers stay on the sheet.

## 6. Export

Ask for the PDF. The "Approved by" box stays empty until you tell the agent to approve.

Next: [how it works](how-it-works.md).
