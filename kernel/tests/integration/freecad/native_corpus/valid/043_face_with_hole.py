# expect: ok
# about: Washer: outer face minus the inner face, then extruded
outer = Part.Face(Part.Wire(Part.makeCircle(20)))
inner = Part.Face(Part.Wire(Part.makeCircle(8)))
face = outer.cut(inner)
feat = doc.addObject("Part::Feature", "Washer")
feat.Shape = face.extrude(Vector(0, 0, 3))
