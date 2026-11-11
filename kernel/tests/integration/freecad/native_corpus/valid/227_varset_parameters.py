# expect: ok
# about: App::VarSet holding named parameters (FreeCAD 1.1)
vs = doc.addObject("App::VarSet", "Vars")
vs.addProperty("App::PropertyLength", "Diameter", "Params", "outer diameter")
vs.Diameter = 24
cyl = doc.addObject("Part::Cylinder", "Shaft")
cyl.setExpression("Radius", "Vars.Diameter / 2")
cyl.Height = 50
doc.recompute()
