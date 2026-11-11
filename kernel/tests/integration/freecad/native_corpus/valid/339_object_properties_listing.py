# expect: ok
# setup: 001_box
# about: List the properties of an object
box = doc.getObject("Box")
for p in box.PropertiesList[:8]:
    print(p, "=", getattr(box, p))
