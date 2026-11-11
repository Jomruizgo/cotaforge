# expect: ok
# about: Part::Chamfer on one edge of a box
box = doc.addObject("Part::Box", "Block")
box.Length, box.Width, box.Height = 30, 20, 10
ch = doc.addObject("Part::Chamfer", "Chamfered")
ch.Base = box
ch.Edges = [(2, 1.5, 1.5)]
