# expect: ok
# setup: 113_pd_datum_plane
# about: A second pad whose sketch sits on the datum plane
body = doc.getObject("Body")
datum = doc.getObject("DatumPlane")
sk2 = body.newObject("Sketcher::SketchObject", "BossSketch")
sk2.AttachmentSupport = (datum, [""])
sk2.MapMode = "FlatFace"
sk2.addGeometry(Part.Circle(Vector(0, 0, 0), Vector(0, 0, 1), 5), False)
sk2.addConstraint(Sketcher.Constraint("Diameter", 0, 2 * 5))
sk2.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 3, 0))
sk2.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 3, 0))
boss = body.newObject("PartDesign::Pad", "Boss")
boss.Profile = sk2
boss.Length = 12
doc.recompute()
