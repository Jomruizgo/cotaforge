# expect: rolled_back Sketch with conflicting constraints
# broken: conflicting constraints
# about: two different lengths on the same line
sk = doc.addObject("Sketcher::SketchObject", "Sketch")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(10, 0, 0)))
sk.addConstraint(Sketcher.Constraint("Distance", 0, 10))
sk.addConstraint(Sketcher.Constraint("Distance", 0, 20))
