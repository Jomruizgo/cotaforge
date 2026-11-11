# expect: rolled_back Change that breaks a dependent
# setup: 023_part_fillet
# broken: Rounded
# about: shrinking a box under a fillet that no longer fits
doc.getObject("Block").Height = 0.5
doc.getObject("Block").Width = 0.5
doc.getObject("Block").Length = 0.5
