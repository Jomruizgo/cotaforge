# expect: ok
# about: Shape-level translate/rotate before storing in a Part::Feature
shape = Part.makeBox(10, 20, 30)
shape.translate(Vector(5, 5, 5))
shape.rotate(Vector(0, 0, 0), Vector(0, 0, 1), 30)
feat = doc.addObject("Part::Feature", "Moved")
feat.Shape = shape
