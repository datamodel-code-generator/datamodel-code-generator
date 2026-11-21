"""Sweep find (_target_render.py:37, _requirements.py:280): a module-level comprehension walrus binds a module name."""

VALUES = [1, 2, 3]
FOUND = [last for v in VALUES if (last := v)]
GEN = sum(total := v for v in VALUES)
NESTED = [[deep := w for w in row] for row in [VALUES]]
