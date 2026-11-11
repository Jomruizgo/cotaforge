# expect: ok
# about: Boolean through the shape API (Shape.cut) into a Part::Feature
base = Part.makeBox(30, 30, 10)
hole = Part.makeCylinder(6, 20, Vector(15, 15, -5))
out = doc.addObject("Part::Feature", "Cutout")
out.Shape = base.cut(hole)
