# expect: rolled_back Sketch with redundant constraints
# broken: redundant constraints
# about: the same constraint added twice
sk = doc.addObject("Sketcher::SketchObject", "Sketch")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(10, 0, 0)))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
