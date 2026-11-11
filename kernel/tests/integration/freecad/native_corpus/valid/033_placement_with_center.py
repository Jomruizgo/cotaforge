# expect: ok
# about: Placement that rotates about a given center
box = doc.addObject("Part::Box", "Pivoted")
box.Placement = Placement(Vector(0, 0, 0), Rotation(Vector(0, 0, 1), 45), Vector(5, 5, 0))
