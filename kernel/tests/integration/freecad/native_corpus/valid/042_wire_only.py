# expect: ok
# about: A closed wire left as a wire (no solid)
wire = Part.makePolygon([Vector(0, 0, 0), Vector(10, 0, 0), Vector(10, 10, 0), Vector(0, 10, 0), Vector(0, 0, 0)])
feat = doc.addObject("Part::Feature", "Outline")
feat.Shape = wire
