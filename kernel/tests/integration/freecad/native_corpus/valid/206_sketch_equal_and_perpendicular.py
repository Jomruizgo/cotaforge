# expect: ok
# about: Equal length and perpendicular lines
sk = doc.addObject("Sketcher::SketchObject", "Square")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(10, 0, 0)), False)
sk.addGeometry(Part.LineSegment(Vector(10, 0, 0), Vector(10, 10, 0)), False)
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
sk.addConstraint(Sketcher.Constraint("Perpendicular", 0, 1))
sk.addConstraint(Sketcher.Constraint("Equal", 0, 1))
doc.recompute()
