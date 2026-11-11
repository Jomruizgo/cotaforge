# expect: ok
# about: A recursive helper that stacks boxes
def tower(level, z, size):
    if level == 0:
        return
    b = doc.addObject("Part::Box", f"Level{level}")
    b.Length = b.Width = size
    b.Height = 5
    b.Placement = Placement(Vector((10 - size) / 2, (10 - size) / 2, z), Rotation())
    tower(level - 1, z + 5, size * 0.8)

tower(5, 0, 10)
