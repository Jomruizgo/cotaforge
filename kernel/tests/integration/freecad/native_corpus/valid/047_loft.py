# expect: ok
# about: Loft between a circle and a square wire
w1 = Part.Wire(Part.makeCircle(10, Vector(0, 0, 0)))
w2 = Part.makePolygon([Vector(-8, -8, 30), Vector(8, -8, 30), Vector(8, 8, 30), Vector(-8, 8, 30), Vector(-8, -8, 30)])
loft = Part.makeLoft([w1, w2], True)
feat = doc.addObject("Part::Feature", "Loft")
feat.Shape = loft
