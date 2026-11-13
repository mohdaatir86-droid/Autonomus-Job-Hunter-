"""Private original PDF storage in the same durable database as the profile."""
import base64
import hashlib
from datetime import datetime, timezone

from database import connect


def initialize():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS outreach_resume (
            id INTEGER PRIMARY KEY CHECK(id=1), filename TEXT NOT NULL,
            content_b64 TEXT NOT NULL, sha256 TEXT NOT NULL, version INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL, size_bytes INTEGER NOT NULL)''')


def save(db, filename, content):
    # Called only after PDF parsing/size validation, in the profile update transaction.
    digest = hashlib.sha256(content).hexdigest()
    db.execute('''INSERT INTO outreach_resume(id,filename,content_b64,sha256,version,uploaded_at,size_bytes)
        VALUES (1,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET
        filename=excluded.filename,content_b64=excluded.content_b64,sha256=excluded.sha256,
        version=outreach_resume.version+1,uploaded_at=excluded.uploaded_at,size_bytes=excluded.size_bytes''',
        (filename,base64.b64encode(content).decode(),digest,datetime.now(timezone.utc).isoformat(),len(content)))


def get(db=None, include_bytes=False):
    if db is None:
        with connect() as connection:
            return get(connection, include_bytes)
    row = db.execute('SELECT * FROM outreach_resume WHERE id=1').fetchone()
    if not row:
        return None
    result = dict(row)
    encoded = result.pop('content_b64')
    if include_bytes:
        content = base64.b64decode(encoded, validate=True)
        if not content.startswith(b'%PDF') or hashlib.sha256(content).hexdigest() != result['sha256']:
            raise ValueError('Stored resume integrity check failed. Upload your PDF again.')
        result['content'] = content
    return result
