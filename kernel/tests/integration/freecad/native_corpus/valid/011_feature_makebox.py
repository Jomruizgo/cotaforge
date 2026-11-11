# expect: ok
# about: Part.makeBox assigned to a Part::Feature
feat = doc.addObject("Part::Feature", "RawBox")
feat.Shape = Part.makeBox(15, 25, 35)
