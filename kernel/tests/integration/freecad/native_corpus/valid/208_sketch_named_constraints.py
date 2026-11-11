# expect: ok
# about: Named dimensional constraints, then read back
sk = doc.addObject("Sketcher::SketchObject", "Named")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(25, 0, 0)), False)
i = sk.addConstraint(Sketcher.Constraint("Distance", 0, 25))
sk.renameConstraint(i, "span")
doc.recompute()
print("span =", sk.getDatum("span"))
