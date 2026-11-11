# expect: ok
# setup: 100_pd_body_pad
# about: PartDesign Hole from a circle sketch on the top face plane
body = doc.getObject("Body")
xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
sk2 = body.newObject("Sketcher::SketchObject", "HoleSketch")
sk2.AttachmentSupport = (xy, [""])
sk2.MapMode = "FlatFace"
sk2.AttachmentOffset = Placement(Vector(0, 0, 10), Rotation())
sk2.addGeometry(Part.Circle(Vector(10, 0, 0), Vector(0, 0, 1), 3), False)
sk2.addConstraint(Sketcher.Constraint("Diameter", 0, 2 * 3))
sk2.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 3, 10))
sk2.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 3, 0))
hole = body.newObject("PartDesign::Hole", "Hole")
hole.Profile = sk2
hole.Diameter = 6
hole.DepthType = "Dimension"
hole.Depth = 6
doc.recompute()
