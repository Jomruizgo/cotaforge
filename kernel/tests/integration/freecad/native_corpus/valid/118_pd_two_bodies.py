# expect: ok
# about: Two independent bodies in one step
for n, r in (("BodyA", 5), ("BodyB", 8)):
    body = doc.addObject("PartDesign::Body", n)
    xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
    sk = body.newObject("Sketcher::SketchObject", f"{n}Sketch")
    sk.AttachmentSupport = (xy, [""])
    sk.MapMode = "FlatFace"
    sk.addGeometry(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), r), False)
    sk.addConstraint(Sketcher.Constraint("Diameter", 0, 2 * r))
    sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 3, 0))
    sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 3, 0))
    pad = body.newObject("PartDesign::Pad", f"{n}Pad")
    pad.Profile = sk
    pad.Length = 10
body_b = doc.getObject("BodyB")
body_b.Placement = Placement(Vector(30, 0, 0), Rotation())
doc.recompute()
