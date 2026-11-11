# expect: ok
# about: A table filled by a loop
sheet = doc.addObject("Spreadsheet::Sheet", "Table")
for i in range(1, 11):
    sheet.set(f"A{i}", str(i))
    sheet.set(f"B{i}", f"={i} * {i}")
doc.recompute()
print(sheet.get("B10"))
