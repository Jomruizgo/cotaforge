# expect: ok
# about: A helper defined in the snippet and called twice
def make_plate(name, w, d, h, x=0):
    plate = doc.addObject("Part::Box", name)
    plate.Length, plate.Width, plate.Height = w, d, h
    plate.Placement = Placement(Vector(x, 0, 0), Rotation())
    return plate

small = make_plate("Small", 10, 10, 2)
large = make_plate("Large", 40, 25, 4, x=20)
