# expect: rolled_back IndexError on a face that does not exist
# error: IndexError
# about: faces index out of range
box = doc.addObject("Part::Box", "Box")
doc.recompute()
face = box.Shape.Faces[99]
