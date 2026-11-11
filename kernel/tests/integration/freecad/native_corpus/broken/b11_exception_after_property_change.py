# expect: rolled_back Exception after changing an existing object
# setup: 001_box
# error: RuntimeError
# about: property changed, then raise
box = doc.getObject("Box")
box.Length = 99
box.Placement = Placement(Vector(5, 5, 5), Rotation())
raise RuntimeError("changed my mind")
