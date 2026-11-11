# expect: ok
# setup: 002_cylinder
# about: Print the bounding box and element counts
shape = doc.getObject("Cylinder").Shape
bb = shape.BoundBox
print(bb.XMin, bb.XMax, bb.ZMax)
print(len(shape.Faces), len(shape.Edges), len(shape.Vertexes))
