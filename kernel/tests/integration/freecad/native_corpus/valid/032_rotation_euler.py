# expect: ok
# about: Rotation from yaw/pitch/roll
box = doc.addObject("Part::Box", "Tilted")
box.Placement = Placement(Vector(10, 0, 0), Rotation(30, 15, 5))
