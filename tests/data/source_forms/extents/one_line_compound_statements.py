"""C3 P1: one-line def, class, if, for and try statements share a line between header and suite."""


def f(): return 1
class C: pass
if f(): x = 1
for i in range(2): x = i
while False: break
with open(__file__) as h: pass
