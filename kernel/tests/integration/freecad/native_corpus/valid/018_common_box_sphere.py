# expect: ok
# about: Part::Common: box intersected with a sphere
box = doc.addObject("Part::Box", "Cube")
box.Length = box.Width = box.Height = 20
ball = doc.addObject("Part::Sphere", "Ball")
ball.Radius = 12
ball.Placement = Placement(Vector(10, 10, 10), Rotation())
common = doc.addObject("Part::Common", "Common")
common.Base = box
common.Tool = ball
