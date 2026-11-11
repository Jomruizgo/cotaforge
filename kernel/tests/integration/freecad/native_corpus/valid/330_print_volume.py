# expect: ok
# setup: 001_box
# about: Print the volume of a solid
box = doc.getObject("Box")
print("volume", box.Shape.Volume)
print("area", box.Shape.Area)
