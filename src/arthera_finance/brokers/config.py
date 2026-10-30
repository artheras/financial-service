"""Host-owned local profiles. Secrets are environment references, never MCP inputs."""
import json
import os
from pathlib import Path
import re
import stat

BROKERS_CONFIG_PATH = Path(os.getenv("FINANCIAL_SERVICE_HOME") or "~/.arthera-finance").expanduser() / "brokers.json"
SECRET_FIELDS = {"api_key", "api_secret", "app_key", "app_secret", "access_token",
                 "private_key", "username", "password", "mfa", "device_id", "trade_password", "unlock_password",
                 "comm_password"}


def load_profiles():
    if not BROKERS_CONFIG_PATH.exists():
        return []
    fd = os.open(BROKERS_CONFIG_PATH, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 131072:
            raise ValueError("Invalid broker profile file")
        if os.name != "nt" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
            raise ValueError("Broker profiles require owner-only permissions")
        document = json.load(stream)
    if not isinstance(document, dict) or not isinstance(document.get("brokers"), list):
        raise ValueError("Broker profiles require a brokers array")
    rows = document["brokers"]
    if len(rows) > 32:
        raise ValueError("At most 32 connections are supported")
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(row.get("id", "")))
                or row["id"] in ids or not isinstance(row.get("type"), str)):
            raise ValueError("Invalid or duplicate connection identifier")
        ids.add(row["id"])
        if "label" in row and (not isinstance(row["label"], str) or len(row["label"]) > 120):
            raise ValueError("Invalid connection label")
        if any(key in row and type(row[key]) is not bool for key in ("paper", "sandbox")):
            raise ValueError("paper and sandbox must be explicit booleans")
        if "trd_env" in row and row["trd_env"] not in {"REAL", "SIMULATE"}:
            raise ValueError("trd_env must be REAL or SIMULATE")
        if SECRET_FIELDS.intersection(row):
            raise ValueError("Store credential environment references, not inline secrets")
        refs = row.get("credentials", {})
        if not isinstance(refs, dict) or any(
                k not in SECRET_FIELDS or not isinstance(v, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", v)
                for k, v in refs.items()):
            raise ValueError("Invalid credential references")
    return rows


def resolve_profile(profile):
    result = {key: value for key, value in profile.items() if key != "credentials"}
    for field, variable in profile.get("credentials", {}).items():
        value = os.environ.get(variable)
        if not value:
            raise ValueError("Required broker credential is unavailable")
        result[field] = value
    return result
