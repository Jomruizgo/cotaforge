# expect: ok
# about: Plane section of a solid via Shape.slice
solid = Part.makeCylinder(10, 30)
wires = solid.slice(Vector(0, 0, 1), 15)
out = doc.addObject("Part::Feature", "Slice")
out.Shape = Part.Compound(wires)
