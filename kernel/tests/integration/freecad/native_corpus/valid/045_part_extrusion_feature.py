# expect: ok
# about: Part::Extrusion feature over a face object
base = doc.addObject("Part::Feature", "BaseFace")
base.Shape = Part.Face(Part.makePolygon([Vector(0, 0, 0), Vector(20, 0, 0), Vector(10, 15, 0), Vector(0, 0, 0)]))
ex = doc.addObject("Part::Extrusion", "Prism")
ex.Base = base
ex.DirMode = "Custom"
ex.Dir = Vector(0, 0, 1)
ex.LengthFwd = 12
ex.Solid = True
