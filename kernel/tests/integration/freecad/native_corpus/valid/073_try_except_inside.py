# expect: ok
# about: try/except inside the fragment handled by the fragment itself
try:
    doc.getObject("DoesNotExist").Label
except AttributeError:
    box = doc.addObject("Part::Box", "Fallback")
