# expect: ok
# about: Two parallel lines at a given distance
sk = doc.addObject("Sketcher::SketchObject", "Parallel")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(20, 0, 0)), False)
sk.addGeometry(Part.LineSegment(Vector(0, 9, 0), Vector(20, 11, 0)), False)
sk.addConstraint(Sketcher.Constraint("Parallel", 0, 1))
sk.addConstraint(Sketcher.Constraint("Distance", 1, 1, 0, 8))
doc.recompute()
