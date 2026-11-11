# expect: ok
# about: Part::Fuse of two overlapping boxes
a = doc.addObject("Part::Box", "A")
b = doc.addObject("Part::Box", "B")
b.Placement = Placement(Vector(5, 5, 0), Rotation())
fuse = doc.addObject("Part::Fuse", "Fuse")
fuse.Base = a
fuse.Tool = b
