# expect: rolled_back Exception after the code opened its own transaction
# error: ValueError
# about: stray undo steps are unwound
doc.openTransaction("my own")
doc.addObject("Part::Box", "Mine")
doc.commitTransaction()
doc.openTransaction("another")
doc.addObject("Part::Cylinder", "Mine2")
raise ValueError("boom")
