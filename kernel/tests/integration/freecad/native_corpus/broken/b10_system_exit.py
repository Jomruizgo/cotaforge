# expect: rolled_back SystemExit from the fragment
# error: SystemExit
# about: sys.exit must not stop the worker
import sys
doc.addObject("Part::Box", "Box")
sys.exit(2)
