# expect: ok
# setup: 100_pd_body_pad
# about: Pocket through all material
body = doc.getObject("Body")
xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
sk2 = body.newObject("Sketcher::SketchObject", "ThroughSketch")
sk2.AttachmentSupport = (xy, [""])
sk2.MapMode = "FlatFace"
sk2.AttachmentOffset = Placement(Vector(0, 0, 10), Rotation())
sk2.addGeometry(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), 4), False)
sk2.addConstraint(Sketcher.Constraint("Diameter", 0, 2 * 4))
sk2.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 3, 0))
sk2.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 3, 0))
pocket = body.newObject("PartDesign::Pocket", "ThroughHole")
pocket.Profile = sk2
pocket.Type = "ThroughAll"
doc.recompute()
