# expect: ok
# about: Shape.makeChamfer on the top edges
box = Part.makeBox(30, 20, 10)
top = [e for e in box.Edges if abs(e.Vertexes[0].Z - 10) < 1e-6 and abs(e.Vertexes[-1].Z - 10) < 1e-6]
cut = box.makeChamfer(1.5, top)
feat = doc.addObject("Part::Feature", "Chamfered")
feat.Shape = cut
