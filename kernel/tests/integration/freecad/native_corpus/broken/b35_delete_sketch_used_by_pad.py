# expect: rolled_back Deleting the sketch a pad depends on
# setup: 100_pd_body_pad
# broken: Pad
# about: the profile of a pad is removed
doc.getObject("Body").removeObject(doc.getObject("Sketch"))
doc.removeObject("Sketch")
