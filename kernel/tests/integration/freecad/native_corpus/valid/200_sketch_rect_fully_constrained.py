# expect: ok
# about: Rectangle sketch, fully constrained (DoF 0)
sk = doc.addObject("Sketcher::SketchObject", "Rect")
pts = [Vector(0, 0, 0), Vector(30, 0, 0), Vector(30, 20, 0), Vector(0, 20, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
sk.addConstraint(Sketcher.Constraint("Vertical", 1))
sk.addConstraint(Sketcher.Constraint("Vertical", 3))
sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 30))
sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 20))
sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, 0))
sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, 0))
doc.recompute()
print("DoF", sk.solve())
