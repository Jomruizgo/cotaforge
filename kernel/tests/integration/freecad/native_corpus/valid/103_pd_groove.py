# expect: ok
# setup: 102_pd_revolution
# about: Groove: cut a revolved notch into a revolution
body = doc.getObject("Body")
xz = [o for o in body.Origin.OriginFeatures if o.Role == "XZ_Plane"][0]
sk2 = body.newObject("Sketcher::SketchObject", "GrooveSketch")
sk2.AttachmentSupport = (xz, [""])
sk2.MapMode = "FlatFace"
pts = [Vector(12, 15, 0), Vector(18, 15, 0), Vector(18, 18, 0), Vector(12, 18, 0)]
for i in range(4):
    sk2.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
for i in range(4):
    sk2.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
for i in (0, 2):
    sk2.addConstraint(Sketcher.Constraint("Horizontal", i))
for i in (1, 3):
    sk2.addConstraint(Sketcher.Constraint("Vertical", i))
sk2.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, pts[1].x - pts[0].x))
sk2.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, pts[2].y - pts[1].y))
sk2.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, pts[0].x))
sk2.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, pts[0].y))
groove = body.newObject("PartDesign::Groove", "Groove")
groove.Profile = sk2
groove.ReferenceAxis = (sk2, ["V_Axis"])
groove.Angle = 360
doc.recompute()
