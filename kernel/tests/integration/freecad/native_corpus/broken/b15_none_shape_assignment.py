# expect: rolled_back TypeError assigning None as a shape
# about: a Part::Feature given None instead of a Shape
feat = doc.addObject("Part::Feature", "Broken")
feat.Shape = None
