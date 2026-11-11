# expect: ok
# setup: 019_multifuse
# about: Offset a list of objects by an increasing amount
for i, name in enumerate(["Post0", "Post1", "Post2"]):
    o = doc.getObject(name)
    o.Placement = Placement(Vector(i * 15, 5 * i, 0), Rotation())
