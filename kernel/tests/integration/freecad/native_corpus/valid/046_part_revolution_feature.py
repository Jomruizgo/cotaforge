# expect: ok
# about: Part::Revolution feature
base = doc.addObject("Part::Feature", "ProfileFace")
base.Shape = Part.Face(Part.makePolygon([Vector(8, 0, 0), Vector(12, 0, 0), Vector(12, 0, 10), Vector(8, 0, 10), Vector(8, 0, 0)]))
rev = doc.addObject("Part::Revolution", "Ring")
rev.Source = base
rev.Axis = Vector(0, 0, 1)
rev.Base = Vector(0, 0, 0)
rev.Angle = 360
rev.Solid = True
