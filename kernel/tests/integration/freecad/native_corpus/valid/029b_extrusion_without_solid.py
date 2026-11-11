# expect: ok
# about: Part::Extrusion with Solid=False yields a shell (no solid is expected)
base = doc.addObject("Part::Feature", "OpenProfile")
base.Shape = Part.makePolygon([Vector(0, 0, 0), Vector(10, 0, 0), Vector(10, 10, 0)])
ex = doc.addObject("Part::Extrusion", "Shell")
ex.Base = base
ex.DirMode = "Custom"
ex.Dir = Vector(0, 0, 1)
ex.LengthFwd = 10
ex.Solid = False
