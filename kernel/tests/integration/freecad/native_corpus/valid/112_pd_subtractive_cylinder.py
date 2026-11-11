# expect: ok
# setup: 111_pd_additive_box
# about: PartDesign subtractive primitive cuts a bore
body = doc.getObject("Body")
cyl = body.newObject("PartDesign::SubtractiveCylinder", "Bore")
cyl.Radius = 4
cyl.Height = 30
cyl.Placement = Placement(Vector(20, 15, -5), Rotation())
doc.recompute()
