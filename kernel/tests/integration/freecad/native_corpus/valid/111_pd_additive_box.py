# expect: ok
# about: PartDesign additive primitive: AdditiveBox
body = doc.addObject("PartDesign::Body", "Body")
box = body.newObject("PartDesign::AdditiveBox", "AdditiveBox")
box.Length = 30
box.Width = 20
box.Height = 10
doc.recompute()
