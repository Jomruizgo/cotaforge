# expect: ok
# setup: 202_sketch_circle_constrained
# about: Rebuild the geometry of an existing sketch
sk = doc.getObject("Circle")
sk.deleteAllGeometry()
sk.addGeometry(Part.Circle(Vector(15, 15, 0), Vector(0, 0, 1), 9), False)
doc.recompute()
