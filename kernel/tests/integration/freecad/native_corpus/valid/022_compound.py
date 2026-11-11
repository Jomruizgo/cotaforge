# expect: ok
# about: Part::Compound of two separate boxes
a = doc.addObject("Part::Box", "A")
b = doc.addObject("Part::Box", "B")
b.Placement = Placement(Vector(30, 0, 0), Rotation())
comp = doc.addObject("Part::Compound", "Pair")
comp.Links = [a, b]
