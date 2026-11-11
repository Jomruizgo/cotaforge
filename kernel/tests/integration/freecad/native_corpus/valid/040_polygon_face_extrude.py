# expect: ok
# about: Face from a polygon wire, extruded with Shape.extrude
pts = [Vector(0, 0, 0), Vector(30, 0, 0), Vector(30, 10, 0), Vector(10, 10, 0), Vector(10, 30, 0), Vector(0, 30, 0)]
wire = Part.makePolygon(pts + [pts[0]])
face = Part.Face(wire)
solid = face.extrude(Vector(0, 0, 8))
feat = doc.addObject("Part::Feature", "LBracket")
feat.Shape = solid
