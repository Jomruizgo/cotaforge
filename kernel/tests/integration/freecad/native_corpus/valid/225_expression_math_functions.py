# expect: ok
# about: Expression with math functions
cyl = doc.addObject("Part::Cylinder", "Calc")
cyl.setExpression("Radius", "sqrt(144) * 1 mm")
cyl.setExpression("Height", "max(10 mm; 25 mm)")
doc.recompute()
