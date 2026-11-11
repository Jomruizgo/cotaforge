# expect: ok
# about: Rounded profile: two lines and two arcs, closed
sk = doc.addObject("Sketcher::SketchObject", "Slot")
r = 5
L = 30
sk.addGeometry(Part.LineSegment(Vector(0, -r, 0), Vector(L, -r, 0)), False)
sk.addGeometry(Part.ArcOfCircle(Part.Circle(Vector(L, 0, 0), Vector(0, 0, 1), r), -math.pi / 2, math.pi / 2), False)
sk.addGeometry(Part.LineSegment(Vector(L, r, 0), Vector(0, r, 0)), False)
sk.addGeometry(Part.ArcOfCircle(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), r), math.pi / 2, 3 * math.pi / 2), False)
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
sk.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 2, 1))
sk.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 3, 1))
sk.addConstraint(Sketcher.Constraint("Coincident", 3, 2, 0, 1))
doc.recompute()
