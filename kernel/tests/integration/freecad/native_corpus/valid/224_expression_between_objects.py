# expect: ok
# about: Expression binding one object's dimension to another's
a = doc.addObject("Part::Box", "Master")
a.Length = 20
b = doc.addObject("Part::Box", "Follower")
b.setExpression("Length", "Master.Length * 2")
b.setExpression("Placement.Base.x", "Master.Length + 5 mm")
doc.recompute()
