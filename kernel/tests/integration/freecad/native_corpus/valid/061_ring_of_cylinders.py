# expect: ok
# about: Cylinders on a circle using trigonometry
import math
count = 8
radius = 30
for i in range(count):
    a = 2 * math.pi * i / count
    c = doc.addObject("Part::Cylinder", f"Pin{i}")
    c.Radius = 2
    c.Height = 10
    c.Placement = Placement(Vector(radius * math.cos(a), radius * math.sin(a), 0), Rotation())
