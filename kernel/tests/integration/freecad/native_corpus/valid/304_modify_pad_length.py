# expect: ok
# setup: 100_pd_body_pad
# about: Change the length of an existing pad
doc.getObject("Pad").Length = 25
doc.recompute()
