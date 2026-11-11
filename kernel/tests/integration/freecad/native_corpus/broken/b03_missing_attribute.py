# expect: rolled_back AttributeError on a missing object
# error: AttributeError
# about: getObject returns None and the code reads a property
length = doc.getObject("Nope").Length
