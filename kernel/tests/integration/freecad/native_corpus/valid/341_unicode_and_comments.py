# expect: ok
# about: Unicode text in labels and comments
# pieza: soporte con tolerancia 0.1 mm
box = doc.addObject("Part::Box", "Soporte")
box.Label = "Soporte (pieza única)"
print("cota:", box.Length, "±0.1")
