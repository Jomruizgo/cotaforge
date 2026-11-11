# expect: rolled_back Exception after deleting an existing object
# setup: 001_box
# error: RuntimeError
# about: object deleted, then raise
doc.removeObject("Box")
raise RuntimeError("too late")
