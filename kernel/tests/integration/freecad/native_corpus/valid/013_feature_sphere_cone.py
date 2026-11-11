# expect: ok
# about: makeSphere and makeCone in one step
a = doc.addObject("Part::Feature", "RawSphere")
a.Shape = Part.makeSphere(9)
b = doc.addObject("Part::Feature", "RawCone")
b.Shape = Part.makeCone(8, 2, 20, Vector(40, 0, 0))
