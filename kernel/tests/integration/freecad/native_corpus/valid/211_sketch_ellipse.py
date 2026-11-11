# expect: ok
# about: An ellipse in a sketch
sk = doc.addObject("Sketcher::SketchObject", "Ellipse")
sk.addGeometry(Part.Ellipse(Vector(0, 0, 0), 12, 6), False)
doc.recompute()
