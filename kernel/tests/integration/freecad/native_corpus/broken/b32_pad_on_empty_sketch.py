# expect: rolled_back PartDesign Pad with an empty profile
# broken: recompute error
# about: a pad whose sketch has no geometry
body = doc.addObject("PartDesign::Body", "Body")
sk = body.newObject("Sketcher::SketchObject", "Sketch")
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
