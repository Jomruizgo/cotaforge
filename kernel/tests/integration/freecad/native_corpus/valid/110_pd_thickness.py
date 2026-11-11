# expect: ok
# setup: 100_pd_body_pad
# about: Thickness: hollow the pad leaving the top open
body = doc.getObject("Body")
pad = doc.getObject("Pad")
top = faces_where(pad, lambda f: abs(f.CenterOfMass.z - 10) < 1e-6)
th = body.newObject("PartDesign::Thickness", "Thickness")
th.Base = (pad, [name for name, _ in top])
th.Value = 2
th.Reversed = True
doc.recompute()
