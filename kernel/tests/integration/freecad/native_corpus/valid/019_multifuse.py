# expect: ok
# about: Part::MultiFuse of three cylinders
cyls = []
for i in range(3):
    c = doc.addObject("Part::Cylinder", f"Post{i}")
    c.Radius = 4
    c.Height = 20
    c.Placement = Placement(Vector(i * 6, 0, 0), Rotation())
    cyls.append(c)
fuse = doc.addObject("Part::MultiFuse", "Posts")
fuse.Shapes = cyls
