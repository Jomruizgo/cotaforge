# expect: ok
# setup: 019_multifuse
# about: Delete every object whose name starts with Post
for name in [o.Name for o in doc.Objects if o.Name.startswith("Post")]:
    doc.removeObject(name)
