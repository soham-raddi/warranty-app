"""Starts Digital Twin. Vercel serves the `app` below; locally, run `python app.py`. The code lives in digital_twin/."""
import os

from digital_twin import app

if __name__ == '__main__':
    # Running app.py directly is local development: allow OAuth over plain http://127.0.0.1.
    os.environ.setdefault('AUTHLIB_INSECURE_TRANSPORT', '1')
    debug = os.getenv('FLASK_DEBUG', '0').lower() in {'1', 'true', 'yes'}
    app.run(debug=debug, port=int(os.getenv('PORT', '5000')))
