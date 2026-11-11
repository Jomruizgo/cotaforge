# expect: ok
# setup: 001_box
# about: Printing only: nothing changes in the document
print("objects:", len(doc.Objects))
print([o.Name for o in doc.Objects])
