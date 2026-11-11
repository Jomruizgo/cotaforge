# expect: rolled_back Solid with negative volume
# broken: negative volume
# about: an inside-out box
shape = Part.makeBox(10, 10, 10)
shape.reverse()
feat = doc.addObject("Part::Feature", "InsideOut")
feat.Shape = shape
