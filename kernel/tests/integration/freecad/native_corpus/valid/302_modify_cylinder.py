# expect: ok
# setup: 002_cylinder
# about: Change radius and height of a cylinder
cyl = doc.getObject("Cylinder")
cyl.Radius = cyl.Radius * 2
cyl.Height = 5
