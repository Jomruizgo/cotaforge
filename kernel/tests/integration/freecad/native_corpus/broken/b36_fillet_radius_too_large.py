# expect: rolled_back Fillet radius larger than the part
# broken: TooRound
# about: Part::Fillet with an impossible radius
box = doc.addObject("Part::Box", "Block")
fil = doc.addObject("Part::Fillet", "TooRound")
fil.Base = box
fil.Edges = [(1, 50.0, 50.0)]
