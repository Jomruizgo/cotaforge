# expect: ok
# about: Shape.makeFillet on all vertical edges
box = Part.makeBox(30, 20, 10)
vertical = [e for e in box.Edges if abs(e.Vertexes[0].Z - e.Vertexes[-1].Z) > 1e-6]
rounded = box.makeFillet(3, vertical)
feat = doc.addObject("Part::Feature", "Rounded")
feat.Shape = rounded
