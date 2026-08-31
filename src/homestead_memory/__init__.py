"""homestead-memory — verifiable, local-first AI memory.

Stop renting your mind. Own it, and catch it when it rots.
"""

__version__ = "0.4.0"

__all__ = ["Memory", "connect", "__version__"]


def __getattr__(name: str):
    """Import the SDK only when someone actually asks for it (PEP 562).

    `from .sdk import Memory, connect` at module level cost ~27ms of the ~127ms that
    every `hsm hook` invocation paid, and the hook never uses either name. The hook runs
    as a fresh process on EVERY tool call your agent makes, so that import was multiplied
    by the length of the session: on a 100-call run it was ~2.7s spent importing an SDK
    nobody called.

    `from homestead_memory import Memory` still works unchanged; module __getattr__ is
    consulted on attribute miss, so this is invisible to callers.
    """
    if name in ("Memory", "connect"):
        from . import sdk
        return getattr(sdk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
