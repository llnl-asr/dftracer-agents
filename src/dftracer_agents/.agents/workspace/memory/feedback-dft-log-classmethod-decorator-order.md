---
name: feedback-dft-log-classmethod-decorator-order
description: @_dft.log must be innermost relative to @classmethod/@staticmethod, never outermost
metadata:
  type: feedback
---

Stacking `@_dft.log` ABOVE `@classmethod` (or `@staticmethod`) wraps the classmethod/staticmethod descriptor object itself rather than the underlying function, raising `TypeError: dft_fn.log.<locals>._decorator() takes 1 positional argument but 2 were given` on first call — this only surfaces when the method is actually invoked, so it can pass a syntax/import check and still break at runtime.

**Why:** `dft_fn.log`'s wrapper does `inspect.getfullargspec()`/positional dispatch expecting a plain function, not a descriptor.

**How to apply:** Always order dftracer Python annotations as:
```python
@classmethod
@_dft.log
def method(cls, ...): ...
```
never the reverse. Same rule applies to `@staticmethod`. See also the `@lru_cache` stacking pitfall in [[bug_annotator_fabricated_report]] / dftracer-annotate-python Rule 3 — same class of bug (decorator wraps a non-plain-function object).
