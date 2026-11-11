# expect: ok
# about: Sweep a circular profile along a polyline path
path = Part.makePolygon([Vector(0, 0, 0), Vector(0, 0, 30), Vector(20, 0, 50)])
profile = Part.Wire(Part.makeCircle(3, Vector(0, 0, 0), Vector(0, 0, 1)))
pipe = path.makePipeShell([profile], True, False)
feat = doc.addObject("Part::Feature", "Pipe")
feat.Shape = pipe
