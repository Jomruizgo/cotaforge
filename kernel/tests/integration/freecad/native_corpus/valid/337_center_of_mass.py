# expect: ok
# setup: 001_box
# about: Center of mass and inertia of a solid
shape = doc.getObject("Box").Shape
com = shape.CenterOfMass
print(com, shape.MatrixOfInertia.A[:3])
