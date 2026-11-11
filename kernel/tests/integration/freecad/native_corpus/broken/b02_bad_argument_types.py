# expect: rolled_back TypeError from a FreeCAD call
# error: TypeError
# about: Part.makeBox with a string argument
shape = Part.makeBox("wide", 2, 3)
