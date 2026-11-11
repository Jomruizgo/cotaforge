# expect: ok
# setup: 002_cylinder
# about: Shape.check on a valid solid
shape = doc.getObject("Cylinder").Shape
shape.check(True)
print("valid:", shape.isValid(), "closed:", shape.isClosed())
