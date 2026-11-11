# expect: ok
# setup: 001_box
# about: edges_where picks vertical edges, Part::Fillet rounds them
box = doc.getObject("Box")
vertical = edges_where(box, lambda e: abs(e.Vertexes[0].Z - e.Vertexes[-1].Z) > 1e-6)
fil = doc.addObject("Part::Fillet", "VerticalFillet")
fil.Base = box
fil.Edges = [(int(name[4:]), 2.0, 2.0) for name, _ in vertical]
