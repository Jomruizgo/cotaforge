# expect: ok
# setup: 208_sketch_named_constraints
# about: Change a named dimension of an existing sketch
sk = doc.getObject("Named")
sk.setDatum("span", App.Units.Quantity("40 mm"))
doc.recompute()
