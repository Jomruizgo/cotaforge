# expect: ok
# about: Spreadsheet with labelled parameters and aliases
sheet = doc.addObject("Spreadsheet::Sheet", "Params")
sheet.set("A1", "width")
sheet.set("B1", "=30 mm")
sheet.setAlias("B1", "width")
sheet.set("A2", "height")
sheet.set("B2", "=12 mm")
sheet.setAlias("B2", "height")
doc.recompute()
