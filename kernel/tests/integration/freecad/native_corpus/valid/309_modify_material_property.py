# expect: ok
# setup: 001_box
# about: Add a custom property and set it
box = doc.getObject("Box")
box.addProperty("App::PropertyString", "Note", "Notes", "free text")
box.Note = "stock 6061-T6"
