# expect: ok
# about: Non-body sketch used as the base of a Part::Extrusion
sk = doc.addObject("Sketcher::SketchObject", "Outline")
pts = [Vector(0, 0, 0), Vector(25, 0, 0), Vector(25, 12, 0), Vector(0, 12, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
for i in (0, 2):
    sk.addConstraint(Sketcher.Constraint("Horizontal", i))
for i in (1, 3):
    sk.addConstraint(Sketcher.Constraint("Vertical", i))
sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, pts[1].x - pts[0].x))
sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, pts[2].y - pts[1].y))
sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, pts[0].x))
sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, pts[0].y))
doc.recompute()
ex = doc.addObject("Part::Extrusion", "Block")
ex.Base = sk
ex.DirMode = "Normal"
ex.LengthFwd = 9
ex.Solid = True
doc.recompute()
