"""Framework-level concerns: errors, envelopes, middleware, observability.

Nothing in this package may import from ``src.service`` or ``src.repository``.
The dependency arrow points inward only, and that rule is enforced by a test.
"""
