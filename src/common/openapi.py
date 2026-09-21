"""OpenAPI metadata.

Kept out of ``main`` so that the description can grow without the application
factory growing with it.
"""

TITLE = "Cache Gateway"

DESCRIPTION = """
A read-through cache service in front of PostgreSQL.

This service exists to demonstrate the four ways a cache fails in production,
and the fix for each:

1. **Penetration** -- lookups for keys that do not exist bypass the cache
   entirely and reach the database. Fixed with a Bloom filter.
2. **Avalanche** -- many keys share a TTL and expire together. Fixed with
   TTL jitter.
3. **Stampede** -- one hot key expires and every concurrent request misses.
   Fixed with in-process request coalescing, and optionally a Redis lock
   across workers.
4. **Consistency** -- a write updates the database but not the cache. Fixed
   with cache-aside invalidation.

Every protection is independently toggleable, so each failure can be
reproduced on a running instance.
"""

CONTACT = {"name": "Sahil Sonker", "url": "https://github.com/sahil00016"}

TAGS_METADATA = [
    {"name": "health", "description": "Liveness, readiness and metrics."},
]
