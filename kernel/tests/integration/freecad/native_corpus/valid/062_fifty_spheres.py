# expect: ok
# about: Fifty objects in one step
for i in range(50):
    s = doc.addObject("Part::Sphere", f"S{i:02d}")
    s.Radius = 1 + (i % 5) * 0.2
    s.Placement = Placement(Vector((i % 10) * 5, (i // 10) * 5, 0), Rotation())
