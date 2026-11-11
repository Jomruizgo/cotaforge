# expect: ok
# about: Lambda and list comprehensions over created objects
make = lambda i: doc.addObject("Part::Cylinder", f"C{i}")
cyls = [make(i) for i in range(5)]
for c, r in zip(cyls, [r for r in range(2, 7)]):
    c.Radius = r
    c.Height = 10 + r
