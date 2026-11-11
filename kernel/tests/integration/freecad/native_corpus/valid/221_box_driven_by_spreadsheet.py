# expect: ok
# setup: 220_spreadsheet_cells
# about: A box whose dimensions come from spreadsheet aliases
box = doc.addObject("Part::Box", "Driven")
box.setExpression("Length", "Params.width")
box.setExpression("Height", "Params.height")
doc.recompute()
