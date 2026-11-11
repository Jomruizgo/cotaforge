# expect: ok
# about: Shape.section of two solids into a Part::Feature (curves, no solid)
a = Part.makeBox(20, 20, 20)
b = Part.makeSphere(12, Vector(10, 10, 10))
out = doc.addObject("Part::Feature", "Section")
out.Shape = a.section(b)
