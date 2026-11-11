# expect: rolled_back Boolean with an empty result
# broken: boolean produced an empty result
# about: a cut whose base is entirely inside the tool
base = doc.addObject("Part::Box", "Small")
base.Length = base.Width = base.Height = 5
tool = doc.addObject("Part::Box", "Big")
tool.Length = tool.Width = tool.Height = 50
tool.Placement = Placement(Vector(-10, -10, -10), Rotation())
cut = doc.addObject("Part::Cut", "Nothing")
cut.Base = base
cut.Tool = tool
