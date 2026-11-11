# expect: ok
# about: Non-uniform scale with Shape.transformGeometry
m = Base.Matrix()
m.scale(2, 1, 0.5)
shape = Part.makeSphere(10).transformGeometry(m)
feat = doc.addObject("Part::Feature", "Squashed")
feat.Shape = shape
