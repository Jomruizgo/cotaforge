# expect: ok
# setup: 001_box
# about: List comprehension over the faces of a shape
box = doc.getObject("Box")
areas = sorted(round(f.Area, 3) for f in box.Shape.Faces)
tops = [i for i, f in enumerate(box.Shape.Faces, 1) if f.normalAt(0, 0).z > 0.99]
print(areas, tops)
