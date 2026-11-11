# expect: rolled_back Timeout of a runaway loop
# error: Timeout
# timeout: 2
# about: an unbounded loop stopped by the soft deadline
box = doc.addObject("Part::Box", "Box")
i = 0
while True:
    i += 1
    box.Label = f"Box{i % 7}"
