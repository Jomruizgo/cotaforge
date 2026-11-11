# expect: rolled_back ZeroDivisionError after several objects
# error: ZeroDivisionError
# about: objects are created, then a division by zero
for i in range(4):
    c = doc.addObject("Part::Cylinder", f"Post{i}")
    c.Radius = 3
ratio = 10 / (len(doc.Objects) - 4)
