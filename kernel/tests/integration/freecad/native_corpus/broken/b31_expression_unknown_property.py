# expect: rolled_back Recompute error: bad binding
# broken: recompute error
# about: an expression that names a property that does not exist
box = doc.addObject("Part::Box", "Box")
box.setExpression("Length", "Nothing.Value")
