# expect: ok
# about: Circle with fixed center and radius
sk = doc.addObject("Sketcher::SketchObject", "Circle")
sk.addGeometry(Part.Circle(Vector(10, 10, 0), Vector(0, 0, 1), 5), False)
sk.addConstraint(Sketcher.Constraint("Radius", 0, 5))
sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 3, 10))
sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 3, 10))
doc.recompute()
