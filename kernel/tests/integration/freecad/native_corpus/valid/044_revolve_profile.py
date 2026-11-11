# expect: ok
# about: Revolve a rectangular profile about the Z axis
profile = Part.makePolygon([Vector(5, 0, 0), Vector(15, 0, 0), Vector(15, 0, 20), Vector(5, 0, 20), Vector(5, 0, 0)])
face = Part.Face(profile)
solid = face.revolve(Vector(0, 0, 0), Vector(0, 0, 1), 360)
feat = doc.addObject("Part::Feature", "Sleeve")
feat.Shape = solid
