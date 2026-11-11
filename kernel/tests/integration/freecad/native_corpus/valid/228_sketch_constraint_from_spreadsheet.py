# expect: ok
# about: A sketch dimension bound to a spreadsheet alias
sheet = doc.addObject("Spreadsheet::Sheet", "SketchParams")
sheet.set("A1", "=40 mm")
sheet.setAlias("A1", "len")
sk = doc.addObject("Sketcher::SketchObject", "Driven")
sk.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(25, 0, 0)), False)
i = sk.addConstraint(Sketcher.Constraint("Distance", 0, 25))
sk.renameConstraint(i, "len")
doc.recompute()
sk.setExpression("Constraints.len", "SketchParams.len")
doc.recompute()
