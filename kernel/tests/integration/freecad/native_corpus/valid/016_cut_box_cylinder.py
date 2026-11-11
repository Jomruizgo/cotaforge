# expect: ok
# about: Part::Cut: box minus a cylinder (hole)
box = doc.addObject("Part::Box", "Plate")
box.Length, box.Width, box.Height = 60, 40, 10
cyl = doc.addObject("Part::Cylinder", "Drill")
cyl.Radius = 5
cyl.Height = 30
cyl.Placement = Placement(Vector(30, 20, -5), Rotation())
cut = doc.addObject("Part::Cut", "PlateWithHole")
cut.Base = box
cut.Tool = cyl
