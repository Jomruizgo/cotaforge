# expect: rolled_back Boolean against a null shape
# about: cut with an empty Part.Shape raises inside OCC
base = Part.makeBox(10, 10, 10)
feat = doc.addObject("Part::Feature", "Cutout")
feat.Shape = base.cut(Part.Shape())
