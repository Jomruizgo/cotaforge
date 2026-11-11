# expect: ok
# about: Hollow out a box through its top face
box = Part.makeBox(30, 30, 20)
top = [f for f in box.Faces if abs(f.CenterOfMass.z - 20) < 1e-6]
hollow = box.makeThickness(top, -2, 1e-3)
feat = doc.addObject("Part::Feature", "Hollow")
feat.Shape = hollow
