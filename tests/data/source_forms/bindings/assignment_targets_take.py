"""What each target of an assignment, a loop, a with item and a walrus takes: items pair with items of one length.

assignments: yes
"""

first, (second, *rest) = 1, (2, 3)
left = right = make()
ok, bad = values
for key, value in {"a": 1}.items():
    pass
for one, two in [(1, 2)]:
    pass
with open("f") as (handle, other):
    pass
[item for item, _ in pairs]
holder.attribute, holder["key"] = 1, 2
total: int = 1
total += 2
bare: int
del first
if (found := make()) is not None:
    print(found)
