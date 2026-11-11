# expect: rolled_back Invalid face from two wires
# broken: invalid shape
# about: Part.Face([outer, inner]) without fixing the orientation
outer = Part.Wire(Part.makeCircle(20))
inner = Part.Wire(Part.makeCircle(8))
feat = doc.addObject("Part::Feature", "Washer")
feat.Shape = Part.Face([outer, inner]).extrude(Vector(0, 0, 3))
