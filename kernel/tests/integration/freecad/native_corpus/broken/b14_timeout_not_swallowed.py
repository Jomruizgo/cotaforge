# expect: rolled_back Timeout through an except-Exception loop
# error: Timeout
# timeout: 2
# about: a loop that swallows Exception still gets interrupted
doc.addObject("Part::Box", "Box")
n = 0
while True:
    try:
        n += 1
        if n % 1000 == 0:
            raise RuntimeError("keep going")
    except Exception:
        pass
