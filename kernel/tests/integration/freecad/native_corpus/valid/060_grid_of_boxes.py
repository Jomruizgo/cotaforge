# expect: ok
# about: A 4x3 grid of boxes from a nested loop
for i in range(4):
    for j in range(3):
        b = doc.addObject("Part::Box", f"Cell_{i}_{j}")
        b.Length = b.Width = 8
        b.Height = 4 + i
        b.Placement = Placement(Vector(i * 10, j * 10, 0), Rotation())
