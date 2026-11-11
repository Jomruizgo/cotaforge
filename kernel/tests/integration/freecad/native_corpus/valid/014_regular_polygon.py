# expect: ok
# about: Part::RegularPolygon wire
poly = doc.addObject("Part::RegularPolygon", "Pentagon")
poly.Polygon = 5
poly.Circumradius = 12
