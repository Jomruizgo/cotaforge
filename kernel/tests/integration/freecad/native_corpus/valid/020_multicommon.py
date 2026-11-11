# expect: ok
# about: Part::MultiCommon of three overlapping boxes
boxes = []
for i in range(3):
    b = doc.addObject("Part::Box", f"Cube{i}")
    b.Length = b.Width = b.Height = 20
    b.Placement = Placement(Vector(i * 4, i * 4, 0), Rotation())
    boxes.append(b)
common = doc.addObject("Part::MultiCommon", "Core")
common.Shapes = boxes
