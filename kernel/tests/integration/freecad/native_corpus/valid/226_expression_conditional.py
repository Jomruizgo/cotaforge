# expect: ok
# about: Expression with a conditional
box = doc.addObject("Part::Box", "Conditional")
box.setExpression("Height", "Length > 5 mm ? 40 mm : 10 mm")
doc.recompute()
