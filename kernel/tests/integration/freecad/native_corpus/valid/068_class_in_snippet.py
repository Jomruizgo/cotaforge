# expect: ok
# about: A small class that builds a parametric part
class Spacer:
    def __init__(self, od, id_, h):
        self.od, self.id_, self.h = od, id_, h

    def build(self, name):
        outer = Part.makeCylinder(self.od / 2, self.h)
        inner = Part.makeCylinder(self.id_ / 2, self.h)
        feat = doc.addObject("Part::Feature", name)
        feat.Shape = outer.cut(inner)
        return feat

Spacer(20, 8, 5).build("Spacer")
