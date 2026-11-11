# expect: ok
# setup: 001_box
# about: Move an object into an App::Part container
part = doc.addObject("App::Part", "Assembly")
part.addObject(doc.getObject("Box"))
part.Placement = Placement(Vector(0, 0, 20), Rotation())
