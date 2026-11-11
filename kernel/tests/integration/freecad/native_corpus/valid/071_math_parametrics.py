# expect: ok
# about: Dimensions computed with math
import math
d = 30
box = doc.addObject("Part::Box", "Block")
box.Length = d
box.Width = d / math.sqrt(2)
box.Height = d * math.sin(math.radians(30))
