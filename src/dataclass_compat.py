from __future__ import annotations

from dataclasses import dataclass as _dataclass


def dataclass(*args, **kwargs):
    try:
        return _dataclass(*args, **kwargs)
    except TypeError:
        compat_kwargs = dict(kwargs)
        compat_kwargs.pop("slots", None)
        return _dataclass(*args, **compat_kwargs)
