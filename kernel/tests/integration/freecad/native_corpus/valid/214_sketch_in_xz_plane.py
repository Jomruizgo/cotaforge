# expect: ok
# about: A sketch placed on the XZ plane by its Placement
sk = doc.addObject("Sketcher::SketchObject", "OnXZ")
sk.Placement = Placement(Vector(0, 0, 0), Rotation(Vector(1, 0, 0), 90))
sk.addGeometry(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), 7), False)
doc.recompute()
