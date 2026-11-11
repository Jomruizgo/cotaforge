# expect: ok
# about: Angle between a line and the X axis
sk = doc.addObject("Sketcher::SketchObject", "Angled")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(10, 8, 0)), False)
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
sk.addConstraint(Sketcher.Constraint("Angle", 0, math.radians(45)))
sk.addConstraint(Sketcher.Constraint("Distance", 0, 14))
doc.recompute()
