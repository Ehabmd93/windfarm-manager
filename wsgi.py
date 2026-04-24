"""
WSGI entry point for gunicorn / Railway / Render.
Runs startup (db.create_all + seed) before serving.
"""
from app import app, startup

startup()

if __name__ == '__main__':
    app.run()
