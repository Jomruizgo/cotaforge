# expect: ok
# about: Mirror a shape across a plane
shape = Part.makeCone(6, 2, 15, Vector(10, 0, 0))
mirrored = shape.mirror(Vector(0, 0, 0), Vector(1, 0, 0))
feat = doc.addObject("Part::Feature", "Mirrored")
feat.Shape = mirrored
