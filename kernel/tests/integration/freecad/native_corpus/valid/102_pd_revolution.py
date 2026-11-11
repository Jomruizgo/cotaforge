# expect: ok
# about: Revolve a profile sketched on the XZ plane about its vertical axis
body = doc.addObject("PartDesign::Body", "Body")
xz = [o for o in body.Origin.OriginFeatures if o.Role == "XZ_Plane"][0]
sk = body.newObject("Sketcher::SketchObject", "Profile")
sk.AttachmentSupport = (xz, [""])
sk.MapMode = "FlatFace"
pts = [Vector(5, 0, 0), Vector(15, 0, 0), Vector(15, 25, 0), Vector(5, 25, 0)]
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
rev = body.newObject("PartDesign::Revolution", "Revolution")
rev.Profile = sk
rev.ReferenceAxis = (sk, ["V_Axis"])
rev.Angle = 360
doc.recompute()
