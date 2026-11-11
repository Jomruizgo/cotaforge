# expect: ok
# setup: 011_feature_makebox
# about: Replace the Shape of a raw feature
feat = doc.getObject("RawBox")
feat.Shape = Part.makeBox(40, 40, 40)
