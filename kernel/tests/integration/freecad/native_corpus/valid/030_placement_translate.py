# expect: ok
# setup: 001_box
# about: Translate an existing box by assigning a whole Placement
box = doc.getObject("Box")
box.Placement = Placement(Vector(100, 50, 25), Rotation())
