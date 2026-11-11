# expect: rolled_back Deleting the base of a fillet
# setup: 023_part_fillet
# broken: recompute error
# about: the box a Part::Fillet depends on is deleted
doc.removeObject("Block")
