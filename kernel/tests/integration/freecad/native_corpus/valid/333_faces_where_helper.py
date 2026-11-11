# expect: ok
# setup: 001_box
# about: faces_where with a predicate; print the sub-element names
box = doc.getObject("Box")
big = faces_where(box, lambda f: f.Area > 300)
print([name for name, _ in big])
