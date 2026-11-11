# expect: ok
# about: Part::Fillet on two edges of a box
box = doc.addObject("Part::Box", "Block")
box.Length, box.Width, box.Height = 30, 20, 10
fil = doc.addObject("Part::Fillet", "Rounded")
fil.Base = box
fil.Edges = [(1, 2.0, 2.0), (3, 2.0, 2.0)]
