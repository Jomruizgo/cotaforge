# expect: ok
# about: A bounded while loop
i = 0
x = 0.0
while i < 6:
    b = doc.addObject("Part::Box", f"Rib{i}")
    b.Length, b.Width, b.Height = 2, 20, 10
    b.Placement = Placement(Vector(x, 0, 0), Rotation())
    x += 5
    i += 1
