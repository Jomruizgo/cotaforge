# expect: ok
# setup: 118_pd_two_bodies
# about: PartDesign::Boolean fusing a second body into the first
body = doc.getObject("BodyA")
other = doc.getObject("BodyB")
bool_ = body.newObject("PartDesign::Boolean", "Fuse")
bool_.Type = "Fuse"
bool_.Group = [other]
doc.recompute()
