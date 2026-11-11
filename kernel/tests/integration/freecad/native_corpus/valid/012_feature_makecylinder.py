# expect: ok
# about: Part.makeCylinder with position and direction
feat = doc.addObject("Part::Feature", "RawCylinder")
feat.Shape = Part.makeCylinder(5, 40, Vector(10, 10, 0), Vector(0, 0, 1))
