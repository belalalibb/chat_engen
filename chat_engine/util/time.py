"""Clock helpers — re-export upstream's utc_now so one definition exists."""

from core.contracts.base import utc_now

__all__ = ["utc_now"]
