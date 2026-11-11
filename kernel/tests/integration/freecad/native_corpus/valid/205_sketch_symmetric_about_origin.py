# expect: ok
# about: Symmetric constraint of a line about the origin
sk = doc.addObject("Sketcher::SketchObject", "Sym")
sk.addGeometry(Part.LineSegment(Vector(-10, -5, 0), Vector(10, 5, 0)), False)
sk.addConstraint(Sketcher.Constraint("Symmetric", 0, 1, 0, 2, -1, 1))
doc.recompute()
