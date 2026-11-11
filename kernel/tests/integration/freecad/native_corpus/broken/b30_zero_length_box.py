# expect: rolled_back Recompute error: length too small
# broken: recompute error
# about: Part::Box with zero length
box = doc.addObject("Part::Box", "Box")
box.Length = 0
