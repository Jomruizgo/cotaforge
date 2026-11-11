# expect: ok
# about: Cut of a fuse: two chained booleans
a = doc.addObject("Part::Box", "A")
a.Length, a.Width, a.Height = 20, 20, 20
b = doc.addObject("Part::Box", "B")
b.Placement = Placement(Vector(15, 0, 0), Rotation())
fuse = doc.addObject("Part::Fuse", "AB")
fuse.Base = a
fuse.Tool = b
hole = doc.addObject("Part::Cylinder", "Hole")
hole.Radius = 3
hole.Height = 40
hole.Placement = Placement(Vector(10, 10, -5), Rotation())
cut = doc.addObject("Part::Cut", "ABMinusHole")
cut.Base = fuse
cut.Tool = hole
