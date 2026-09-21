"""Request-scoped context, propagated without threading arguments everywhere.

A ``ContextVar`` is the asyncio-correct way to do this: each task gets its own
view, so concurrent requests inside one worker cannot read each other's values.
"""

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Return the current request's correlation id.

    Returns:
        The request id, or ``"-"`` outside a request.
    """
    return request_id_var.get()
