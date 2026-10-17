import hashlib
import hmac
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from .config import Settings
from .db import Database


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings
        self.hasher = PasswordHasher()
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    def bootstrap(self):
        if self.db.one("SELECT id FROM owner"):
            return
        path = self.settings.data_dir / "setup-token"
        # No web signup without possession of a local, private one-use token.
        if not path.exists():
            token = secrets.token_urlsafe(32)
            with path.open("x") as stream:
                path.chmod(0o600)
                stream.write(token)
            self.db.set_setting("setup_token_hash", digest(token))

    def setup(self, username, password, token=None):
        if not username or len(username) > 80 or not 12 <= len(password) <= 1024:
            raise ValueError("Use a username and a password of 12–1024 characters")
        password_hash = self.hasher.hash(password)
        with self.db.transaction() as conn:
            if conn.execute("SELECT id FROM owner").fetchone():
                raise ValueError("Owner account already exists")
            if token is not None:
                row = conn.execute("SELECT value FROM settings WHERE key='setup_token_hash'").fetchone()
                import json

                if not row or not hmac.compare_digest(digest(token), json.loads(row["value"])):
                    raise ValueError("Invalid setup token")
            conn.execute("INSERT INTO owner VALUES(1,?,?)", (username, password_hash))
            conn.execute("DELETE FROM settings WHERE key='setup_token_hash'")
            self.db.event(conn, "owner.created", {})
        (self.settings.data_dir / "setup-token").unlink(missing_ok=True)

    def throttle(self, ip):
        """Count one login or setup attempt from ip; ValueError when it or everyone together is over the limit."""
        now = time.time()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM login_attempts WHERE at < ?", (now - 900,))
            # Global ceiling also limits attacks rotating client IPs.
            if (
                conn.execute("SELECT count(*) FROM login_attempts WHERE ip=?", (ip,)).fetchone()[0] >= 10
                or conn.execute("SELECT count(*) FROM login_attempts").fetchone()[0] >= 100
            ):
                raise ValueError("Too many attempts; try again in 15 minutes")
            conn.execute("INSERT INTO login_attempts VALUES(?,?)", (ip, now))

    def login(self, username, password, ip):
        now = time.time()
        self.throttle(ip)
        owner = self.db.one("SELECT * FROM owner")
        try:
            self.hasher.verify(owner["password_hash"] if owner else self.dummy_hash, password)
            valid = owner and hmac.compare_digest(owner["username"], username)
        except VerificationError:
            valid = False
        if not valid:
            raise ValueError("Invalid username or password")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO sessions VALUES(?,?,?)", (digest(token), csrf, now + self.settings.session_seconds)
            )
            self.db.event(conn, "owner.login", {})
        return token, csrf

    def session(self, token):
        return self.db.one(
            "SELECT csrf,expires_at FROM sessions WHERE token_hash=? AND expires_at>?",
            (digest(token or ""), time.time()),
        )

    def logout(self, token):
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token or ""),))
            self.db.event(conn, "owner.logout", {})
