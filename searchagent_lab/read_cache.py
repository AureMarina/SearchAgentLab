"""Per-request read memoization only: never a stale cross-request result cache.

Nested callers share one bounded cache. Copies prevent callers mutating cached
read models. New requests observe committed writes without TTL waiting.
"""
from __future__ import annotations
import copy
import functools
import json
import threading
from contextlib import contextmanager

_local=threading.local()

@contextmanager
def read_scope():
    outer=not hasattr(_local,'cache')
    if outer:_local.cache={}
    try:yield
    finally:
        if outer:del _local.cache

def request_scope(fn):
    @functools.wraps(fn)
    def wrapper(*args,**kwargs):
        with read_scope():return fn(*args,**kwargs)
    return wrapper

def cached_read(fn):
    @functools.wraps(fn)
    def wrapper(self,*args,**kwargs):
        cache=getattr(_local,'cache',None)
        if cache is None:return fn(self,*args,**kwargs)
        key=(id(self),fn.__qualname__,json.dumps([args,kwargs],sort_keys=True,default=str))
        if key in cache:return copy.deepcopy(cache[key])
        result=fn(self,*args,**kwargs)
        if len(cache)<512:cache[key]=copy.deepcopy(result)
        return result
    return wrapper
