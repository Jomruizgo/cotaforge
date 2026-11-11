# expect: ok
# about: An arc of a circle in a sketch
sk = doc.addObject("Sketcher::SketchObject", "Arc")
sk.addGeometry(Part.ArcOfCircle(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), 10), 0, math.pi / 2), False)
sk.addConstraint(Sketcher.Constraint("Radius", 0, 10))
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 3, -1, 1))
doc.recompute()
