# expect: ok
# about: f-strings for names and labels
base = "Bracket"
for side in ("Left", "Right"):
    b = doc.addObject("Part::Box", f"{base}{side}")
    b.Label = f"{base} ({side.lower()})"
    b.Length = 12
    b.Placement = Placement(Vector(-20 if side == "Left" else 20, 0, 0), Rotation())
