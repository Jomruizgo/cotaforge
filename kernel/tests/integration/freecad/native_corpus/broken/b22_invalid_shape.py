# expect: rolled_back Object with an invalid shape
# broken: invalid shape
# about: a self-intersecting face stored in a Part::Feature
poly = Part.makePolygon([Vector(0, 0, 0), Vector(10, 10, 0), Vector(10, 0, 0), Vector(0, 10, 0), Vector(0, 0, 0)])
feat = doc.addObject("Part::Feature", "Bowtie")
feat.Shape = Part.Face(poly)
