# expect: ok
# about: Offset a box outward
box = Part.makeBox(10, 10, 10)
bigger = box.makeOffsetShape(2, 1e-3)
feat = doc.addObject("Part::Feature", "Offset")
feat.Shape = bigger
