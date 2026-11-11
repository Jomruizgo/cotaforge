# expect: ok
# setup: 101_pd_pocket
# about: Delete the last feature of a body (the pocket) and its sketch
body = doc.getObject("Body")
body.removeObject(doc.getObject("Pocket"))
doc.removeObject("Pocket")
doc.removeObject("PocketSketch")
doc.recompute()
