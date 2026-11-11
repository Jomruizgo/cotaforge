# expect: ok
# setup: 016_cut_box_cylinder
# about: Print InList/OutList of an object
cut = doc.getObject("PlateWithHole")
print([o.Name for o in cut.OutList], [o.Name for o in cut.InList])
