# expect: ok
# setup: 100_pd_body_pad
# about: A datum plane offset from XY inside the body
body = doc.getObject("Body")
xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
plane = body.newObject("PartDesign::Plane", "DatumPlane")
plane.AttachmentSupport = (xy, [""])
plane.MapMode = "FlatFace"
plane.AttachmentOffset = Placement(Vector(0, 0, 5), Rotation())
doc.recompute()
