# expect: ok
# about: Rotate a cylinder 90 degrees about X
cyl = doc.addObject("Part::Cylinder", "Pipe")
cyl.Radius = 5
cyl.Height = 40
cyl.Placement = Placement(Vector(0, 0, 0), Rotation(Vector(1, 0, 0), 90))
