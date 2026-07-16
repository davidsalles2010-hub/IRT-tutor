"""Test environment: isolated SQLite database, admin allow-list, no Resend."""

import os
import tempfile

_dbfile = os.path.join(tempfile.gettempdir(), "mathlens_test.db")
if os.path.exists(_dbfile):
    os.remove(_dbfile)

os.environ["DATABASE_URL"] = f"sqlite:///{_dbfile}"
os.environ["ADMIN_EMAILS"] = "boss@example.com"
os.environ.pop("RESEND_API_KEY", None)
os.environ.pop("GOOGLE_CLIENT_ID", None)

from app.db import init_db  # noqa: E402

init_db()
