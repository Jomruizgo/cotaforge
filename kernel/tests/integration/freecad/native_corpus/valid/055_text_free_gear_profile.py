# expect: ok
# about: Gear-like face from a loop of polygon points
import math
pts = []
teeth = 12
for i in range(teeth * 2):
    a = math.pi * i / teeth
    r = 20 if i % 2 == 0 else 16
    pts.append(Vector(r * math.cos(a), r * math.sin(a), 0))
face = Part.Face(Part.makePolygon(pts + [pts[0]]))
feat = doc.addObject("Part::Feature", "Gear")
feat.Shape = face.extrude(Vector(0, 0, 6))
