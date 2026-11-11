# expect: ok
# setup: 101_pd_pocket
# about: Polar pattern of a pocket about the Z axis
body = doc.getObject("Body")
zaxis = [o for o in body.Origin.OriginFeatures if o.Role == "Z_Axis"][0]
pocket = doc.getObject("Pocket")
pp = body.newObject("PartDesign::PolarPattern", "PolarPattern")
pp.Originals = [pocket]
pp.Axis = (zaxis, [""])
pp.Angle = 360
pp.Occurrences = 4
body.Tip = pp
doc.recompute()
