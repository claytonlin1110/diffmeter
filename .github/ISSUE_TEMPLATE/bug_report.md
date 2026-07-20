---
name: Bug report
about: Something scored wrong, crashed, or behaved unexpectedly
title: ""
labels: bug
---

**What happened**

**What you expected**

**Minimal reproduction**

If this is a scoring bug, the most useful thing you can give us is the
smallest before/after snippet that reproduces it, e.g.:

```python
# before
def f():
    return 1

# after
def f():
    # explain
    return 1
```

and the command you ran (`diffmeter score ...`).

**Environment**

- diffmeter version: `diffmeter --version`
- OS:
- Python version:
