"""Tiny key lookup: env var first, else ~/.config/keys/enrich.env (KEY=value lines).
Returns None if unset — callers skip that source. No deps."""
import os

_ENV = os.path.expanduser("~/.config/keys/enrich.env")
_cache = None


def get(name: str):
    if os.environ.get(name):
        return os.environ[name]
    global _cache
    if _cache is None:
        _cache = {}
        if os.path.exists(_ENV):
            for line in open(_ENV):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    _cache[k.strip()] = v.strip().strip('"').strip("'")
    return _cache.get(name)
