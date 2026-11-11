# expect: ok
# about: Point-on-object constraint on the X axis
sk = doc.addObject("Sketcher::SketchObject", "OnAxis")
sk.addGeometry(Part.LineSegment(Vector(3, 1, 0), Vector(20, 0, 0)), False)
sk.addConstraint(Sketcher.Constraint("PointOnObject", 0, 1, -1))
sk.addConstraint(Sketcher.Constraint("PointOnObject", 0, 2, -1))
doc.recompute()
