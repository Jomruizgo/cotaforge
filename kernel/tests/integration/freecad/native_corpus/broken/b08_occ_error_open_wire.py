# expect: rolled_back OCC error building a face from an open wire
# about: Part.Face from a wire that is not closed
wire = Part.makePolygon([Vector(0, 0, 0), Vector(10, 0, 0), Vector(10, 10, 0)])
face = Part.Face(wire)
feat = doc.addObject("Part::Feature", "Face")
feat.Shape = face
