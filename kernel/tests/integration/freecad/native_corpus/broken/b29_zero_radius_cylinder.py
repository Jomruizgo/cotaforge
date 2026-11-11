# expect: rolled_back Recompute error: radius too small
# broken: recompute error
# about: Part::Cylinder with zero radius
cyl = doc.addObject("Part::Cylinder", "Cylinder")
cyl.Radius = 0
