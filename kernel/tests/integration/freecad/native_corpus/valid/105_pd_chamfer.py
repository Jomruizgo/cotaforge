# expect: ok
# setup: 100_pd_body_pad
# about: Chamfer the top edges of the pad
body = doc.getObject("Body")
pad = doc.getObject("Pad")
top = edges_where(pad, lambda e: abs(e.CenterOfMass.z - 10) < 1e-6)
chamfer = body.newObject("PartDesign::Chamfer", "Chamfer")
chamfer.Base = (pad, [name for name, _ in top])
chamfer.Size = 1.5
doc.recompute()
