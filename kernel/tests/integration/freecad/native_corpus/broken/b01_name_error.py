# expect: rolled_back NameError after creating an object
# error: NameError
# about: an undefined name after a box was created
box = doc.addObject("Part::Box", "Box")
box.Length = 10
total = box.Length + undefined_value
