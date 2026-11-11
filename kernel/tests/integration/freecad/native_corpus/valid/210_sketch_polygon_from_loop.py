# expect: ok
# about: A regular polygon built with a loop of lines and coincident constraints
n = 6
R = 15
pts = [Vector(R * math.cos(2 * math.pi * k / n), R * math.sin(2 * math.pi * k / n), 0) for k in range(n)]
sk = doc.addObject("Sketcher::SketchObject", "Hexagon")
for k in range(n):
    sk.addGeometry(Part.LineSegment(pts[k], pts[(k + 1) % n]), False)
for k in range(n):
    sk.addConstraint(Sketcher.Constraint("Coincident", k, 2, (k + 1) % n, 1))
doc.recompute()
