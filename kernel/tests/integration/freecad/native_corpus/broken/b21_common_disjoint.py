# expect: rolled_back Common of disjoint solids
# broken: boolean produced an empty result
# about: intersection of two boxes that do not touch
a = doc.addObject("Part::Box", "A")
b = doc.addObject("Part::Box", "B")
b.Placement = Placement(Vector(100, 0, 0), Rotation())
common = doc.addObject("Part::Common", "Nothing")
common.Base = a
common.Tool = b
