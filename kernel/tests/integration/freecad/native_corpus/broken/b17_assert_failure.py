# expect: rolled_back AssertionError of a self-check
# error: AssertionError
# about: the fragment checks its own result and fails
box = doc.addObject("Part::Box", "Box")
doc.recompute()
assert box.Shape.Volume > 1e9, "volume sanity check failed"
