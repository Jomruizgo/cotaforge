# expect: rolled_back Recompute error from an expression
# broken: recompute error
# about: a division by zero bound to a property
box = doc.addObject("Part::Box", "Box")
box.setExpression("Length", "1 / 0")
