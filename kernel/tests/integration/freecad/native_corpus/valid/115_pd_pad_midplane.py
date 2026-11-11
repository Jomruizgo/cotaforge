# expect: ok
# about: Pad symmetric to the sketch plane
body = doc.addObject("PartDesign::Body", "Body")
xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
sk = body.newObject("Sketcher::SketchObject", "Sketch")
sk.AttachmentSupport = (xy, [""])
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), 10), False)
sk.addConstraint(Sketcher.Constraint("Diameter", 0, 2 * 10))
sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 3, 0))
sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 3, 0))
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 20
pad.SideType = "Symmetric"
doc.recompute()
