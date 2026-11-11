# expect: ok
# setup: 001_box
# about: Cut a hole in a box created by an earlier step
cyl = doc.addObject("Part::Cylinder", "Drill")
cyl.Radius = 4
cyl.Height = 40
cyl.Placement = Placement(Vector(20, 10, -5), Rotation())
cut = doc.addObject("Part::Cut", "Drilled")
cut.Base = doc.getObject("Box")
cut.Tool = cyl
