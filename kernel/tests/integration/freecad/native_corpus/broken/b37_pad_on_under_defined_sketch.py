# expect: rolled_back a feature built on a sketch that still has degrees of freedom
# broken: sketch_not_fully_defined
# about: a rectangle held only by coincident ends: its size and place are free, so no feature may be built on it
body = doc.addObject("PartDesign::Body", "Body")
sk = body.newObject("Sketcher::SketchObject", "Sketch")
sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
sk.MapMode = "FlatFace"
pts = [Vector(0, 0, 0), Vector(30, 0, 0), Vector(30, 15, 0), Vector(0, 15, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 5
