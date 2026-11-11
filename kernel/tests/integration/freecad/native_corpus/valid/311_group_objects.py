# expect: ok
# setup: 019_multifuse
# about: Put existing objects into an App::DocumentObjectGroup
grp = doc.addObject("App::DocumentObjectGroup", "Posts")
for name in ("Post0", "Post1", "Post2"):
    grp.addObject(doc.getObject(name))
