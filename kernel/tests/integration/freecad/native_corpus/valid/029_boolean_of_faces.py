# expect: ok
# about: Part::Cut of two faces gives a face, not a solid (not an empty result)
a = doc.addObject("Part::Plane", "PlaneA")
a.Length = a.Width = 20
b = doc.addObject("Part::Plane", "PlaneB")
b.Length = b.Width = 10
b.Placement = Placement(Vector(5, 5, 0), Rotation())
cut = doc.addObject("Part::Cut", "FaceCut")
cut.Base = a
cut.Tool = b
