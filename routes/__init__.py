"""Flask route blueprints for the warranty app."""

from .chat import chat_bp
from .pages import pages_bp
from .receipts import receipts_bp
from .settings import settings_bp

__all__ = ["chat_bp", "pages_bp", "receipts_bp", "settings_bp"]