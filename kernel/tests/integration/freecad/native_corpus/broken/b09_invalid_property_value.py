# expect: rolled_back Invalid property value
# about: a string assigned to a length property
box = doc.addObject("Part::Box", "Box")
box.Length = "not a number"
