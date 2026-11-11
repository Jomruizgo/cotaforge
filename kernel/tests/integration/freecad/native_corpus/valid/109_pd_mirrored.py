# expect: ok
# setup: 101_pd_pocket
# about: Mirror a pocket across the YZ plane
body = doc.getObject("Body")
yz = [o for o in body.Origin.OriginFeatures if o.Role == "YZ_Plane"][0]
pocket = doc.getObject("Pocket")
mir = body.newObject("PartDesign::Mirrored", "Mirrored")
mir.Originals = [pocket]
mir.MirrorPlane = (yz, [""])
body.Tip = mir
doc.recompute()
