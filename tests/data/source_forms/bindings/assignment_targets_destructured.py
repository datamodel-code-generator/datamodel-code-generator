"""#7026 Parser.results: names bound through tuple, list, starred and chained assignment targets."""

first, (second, *rest) = 1, (2, 3, 4)
[left, right] = "ab"
top = middle = bottom = 0
pairs = {}
pairs["key"], value = 1, 2
