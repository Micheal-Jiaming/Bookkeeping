"""Settings read/write.

Settings live in the database rather than a config file so the app is
self-contained and editable from its own Settings page. The API key is stored in
plain text in ``data/bookkeeping.db`` -- acceptable for a single-user local
application, and stated plainly in the project document so nobody is surprised.
It is never sent to the browser: reads return a masked form.
"""

from __future__ import annotations

from .db import DEFAULT_SETTINGS, connect

SECRET_KEYS = {"anthropic_api_key"}


def get_all() -> dict[str, str]:
    """Every setting, with defaults filled in for keys added by a later version."""
    with connect() as db:
        rows = db.execute("SELECT key, value FROM setting").fetchall()
    values = dict(DEFAULT_SETTINGS)
    values.update({row["key"]: row["value"] for row in rows})
    return values


def get(key: str, default: str = "") -> str:
    return get_all().get(key, default)


def save(updates: dict[str, str]) -> dict[str, str]:
    """Persist the given keys. Unknown keys are ignored, empty secrets are kept.

    An empty string for a secret means "the browser sent back the masked
    placeholder", not "clear the key". Clearing is done with the explicit
    sentinel value ``__clear__``.
    """
    with connect() as db:
        for key, value in updates.items():
            if key not in DEFAULT_SETTINGS:
                continue
            if key in SECRET_KEYS:
                if value == "__clear__":
                    value = ""
                elif not value or value.startswith("****"):
                    continue
            db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
    return public_view()


def public_view() -> dict[str, str]:
    """Settings safe to send to the browser: secrets replaced by a mask."""
    values = get_all()
    for key in SECRET_KEYS:
        secret = values.get(key) or ""
        values[key] = f"****{secret[-4:]}" if len(secret) > 4 else ("****" if secret else "")
    return values
