# expect: ok
# about: Construction (reference) geometry in a sketch
sk = doc.addObject("Sketcher::SketchObject", "Construction")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(20, 0, 0)), False)
ref = sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(20, 20, 0)), True)
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, ref, 1))
doc.recompute()
