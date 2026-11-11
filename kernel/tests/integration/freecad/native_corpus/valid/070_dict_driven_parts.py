# expect: ok
# about: Parts defined by a dictionary of parameters
specs = {
    "Axle": dict(radius=4, height=60),
    "Hub": dict(radius=12, height=15),
    "Flange": dict(radius=25, height=3),
}
for name, spec in specs.items():
    cyl = doc.addObject("Part::Cylinder", name)
    cyl.Radius = spec["radius"]
    cyl.Height = spec["height"]
