# expect: ok
# setup: 221_box_driven_by_spreadsheet
# about: Changing a cell updates the dependent box
sheet = doc.getObject("Params")
sheet.set("B1", "=55 mm")
doc.recompute()
