# expect: ok
# about: Part::Line and Part::Circle curves
ln = doc.addObject("Part::Line", "Line")
ln.X1, ln.Y1, ln.Z1 = 0, 0, 0
ln.X2, ln.Y2, ln.Z2 = 30, 10, 5
ci = doc.addObject("Part::Circle", "Circle")
ci.Radius = 7
