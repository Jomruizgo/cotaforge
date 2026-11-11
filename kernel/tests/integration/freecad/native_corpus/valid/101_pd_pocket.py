# expect: ok
# setup: 100_pd_body_pad
# about: Pocket a rectangle from the top of the pad
body = doc.getObject("Body")
xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
sk2 = body.newObject("Sketcher::SketchObject", "PocketSketch")
sk2.AttachmentSupport = (xy, [""])
sk2.MapMode = "FlatFace"
sk2.AttachmentOffset = Placement(Vector(0, 0, 10), Rotation())
pts = [Vector(5, -5, 0), Vector(15, -5, 0), Vector(15, 5, 0), Vector(5, 5, 0)]
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
pocket = body.newObject("PartDesign::Pocket", "Pocket")
pocket.Profile = sk2
pocket.Type = "Length"
pocket.Length = 4
doc.recompute()
