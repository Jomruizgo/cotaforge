# expect: ok
# setup: 019_multifuse
# about: Move every object of the document by the same vector
for obj in doc.Objects:
    if hasattr(obj, "Placement"):
        p = obj.Placement
        obj.Placement = Placement(p.Base + Vector(0, 0, 10), p.Rotation)
