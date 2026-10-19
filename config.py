"""Settings shared across modules that depend on where the app runs."""
import os

# Vercel sets VERCEL=1 in every deployment. APP_ENV=production forces production behaviour elsewhere.
IS_PRODUCTION = bool(os.getenv('VERCEL')) or os.getenv('APP_ENV', '').lower() == 'production'

# Vercel rejects request bodies over 4.5 MB before they reach the app, so stay under it there.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 if IS_PRODUCTION else 20 * 1024 * 1024

# Reminder emails need one site-wide email service (not yet set up), so families never have to
# enter their own mail server. Until then they are only available when running locally.
EMAIL_REMINDERS_AVAILABLE = not IS_PRODUCTION
