# expect: ok
# about: Wire from a line, an arc and a line
l1 = Part.LineSegment(Vector(0, 0, 0), Vector(10, 0, 0)).toShape()
arc = Part.Arc(Vector(10, 0, 0), Vector(15, 5, 0), Vector(10, 10, 0)).toShape()
l2 = Part.LineSegment(Vector(10, 10, 0), Vector(0, 10, 0)).toShape()
l3 = Part.LineSegment(Vector(0, 10, 0), Vector(0, 0, 0)).toShape()
face = Part.Face(Part.Wire([l1, arc, l2, l3]))
feat = doc.addObject("Part::Feature", "Rounded2D")
feat.Shape = face.extrude(Vector(0, 0, 4))
