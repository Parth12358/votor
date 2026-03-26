# votor/events.py — thin broadcast shim
# query.py imports this; dashboard.py registers the real sender at startup.
# When running from the REPL (no dashboard), _broadcast_fn is None and all
# calls are no-ops.

_broadcast_fn = None


def register(fn):
    """Called by dashboard.py at startup to wire in broadcast_sync."""
    global _broadcast_fn
    _broadcast_fn = fn


def broadcast(event: dict):
    """Broadcast an event to all connected dashboard clients, or no-op."""
    if _broadcast_fn is not None:
        _broadcast_fn(event)
