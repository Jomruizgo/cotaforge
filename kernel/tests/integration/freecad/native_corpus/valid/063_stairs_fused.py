# expect: ok
# about: Stairs: boxes fused into one solid with MultiFuse
steps = []
for i in range(6):
    b = doc.addObject("Part::Box", f"Step{i}")
    b.Length, b.Width, b.Height = 30, 80, 17 * (i + 1)
    b.Placement = Placement(Vector(30 * i, 0, 0), Rotation())
    steps.append(b)
stairs = doc.addObject("Part::MultiFuse", "Stairs")
stairs.Shapes = steps
