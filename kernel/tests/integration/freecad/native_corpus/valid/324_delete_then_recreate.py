# expect: ok
# setup: 001_box
# about: Delete a box and recreate one with the same name
doc.removeObject("Box")
box = doc.addObject("Part::Box", "Box")
box.Length = 5
