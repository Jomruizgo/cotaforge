# expect: ok
# setup: 001_box
# about: Copy an object with doc.copyObject
copy = doc.copyObject(doc.getObject("Box"), False)
copy.Label = "BoxCopy"
copy.Placement = Placement(Vector(50, 0, 0), Rotation())
