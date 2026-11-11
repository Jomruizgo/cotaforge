# expect: ok
# about: Harmless standard-library imports
import itertools, json, re
data = json.dumps({"pairs": list(itertools.combinations(range(4), 2))})
print(re.sub(r"\s+", " ", data))
