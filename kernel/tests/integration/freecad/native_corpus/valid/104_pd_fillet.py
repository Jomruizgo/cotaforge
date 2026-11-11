# expect: ok
# setup: 100_pd_body_pad
# about: Fillet the vertical edges of the pad, found with edges_where
body = doc.getObject("Body")
pad = doc.getObject("Pad")
vertical = edges_where(pad, lambda e: abs(e.tangentAt(e.FirstParameter).z) > 0.99)
fillet = body.newObject("PartDesign::Fillet", "Fillet")
fillet.Base = (pad, [name for name, _ in vertical])
fillet.Radius = 3
doc.recompute()
