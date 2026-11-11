# expect: ok
# about: A BSpline curve from points
pts = [Vector(0, 0, 0), Vector(10, 8, 0), Vector(20, -4, 0), Vector(30, 6, 0)]
curve = Part.BSplineCurve()
curve.interpolate(pts)
feat = doc.addObject("Part::Feature", "Spline")
feat.Shape = curve.toShape()
