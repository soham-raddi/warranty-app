"""Flask route blueprints for the warranty app."""

from .auth import auth_bp, init_oauth
from .chat import chat_bp
from .pages import pages_bp
from .receipts import receipts_bp
from .settings import settings_bp
from .workspaces import workspaces_bp

__all__ = ["auth_bp", "init_oauth", "chat_bp", "pages_bp", "receipts_bp", "settings_bp", "workspaces_bp"]
