# expect: ok
# setup: 101_pd_pocket
# about: Linear pattern of a pocket along -X
body = doc.getObject("Body")
xaxis = [o for o in body.Origin.OriginFeatures if o.Role == "X_Axis"][0]
pocket = doc.getObject("Pocket")
lp = body.newObject("PartDesign::LinearPattern", "LinearPattern")
lp.Originals = [pocket]
lp.Direction = (xaxis, [""])
lp.Reversed = True
lp.Length = 40
lp.Occurrences = 3
body.Tip = lp
doc.recompute()
