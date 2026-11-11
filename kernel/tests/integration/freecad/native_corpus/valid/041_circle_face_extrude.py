# expect: ok
# about: Circle edge -> wire -> face -> extrusion
circle = Part.makeCircle(12)
face = Part.Face(Part.Wire(circle))
feat = doc.addObject("Part::Feature", "Disc")
feat.Shape = face.extrude(Vector(0, 0, 5))
