# expect: ok
# about: Drill a hole pattern into a plate with a shape-level loop
plate = Part.makeBox(80, 50, 6)
for i in range(4):
    for j in range(2):
        hole = Part.makeCylinder(3, 20, Vector(10 + i * 20, 12 + j * 26, -5))
        plate = plate.cut(hole)
out = doc.addObject("Part::Feature", "DrilledPlate")
out.Shape = plate
