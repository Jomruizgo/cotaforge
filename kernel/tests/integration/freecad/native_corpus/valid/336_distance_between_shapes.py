# expect: ok
# setup: 022_compound
# about: Minimum distance between two shapes
a = doc.getObject("A").Shape
b = doc.getObject("B").Shape
d, pts, info = a.distToShape(b)
print("distance", d)
