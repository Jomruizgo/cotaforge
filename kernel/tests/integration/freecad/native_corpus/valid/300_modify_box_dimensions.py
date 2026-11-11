# expect: ok
# setup: 001_box
# about: Change the dimensions of an existing box
box = doc.getObject("Box")
box.Length = 80
box.Height = 3
