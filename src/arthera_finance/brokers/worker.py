"""Isolate optional broker SDK output, timeouts and connection lifetimes from MCP."""
import contextlib
import json
import os
import subprocess
import sys
import threading

_QUERY_LOCK = threading.Lock()


def call(connection_id, operation, status="all", limit=50):
    from .config import load_profiles
    from .service import enabled
    if not enabled():
        return {"success": False, "error_code": "broker_access_disabled"}
    try:
        profile = next((p for p in load_profiles() if p["id"] == connection_id), None)
        if profile is None:
            return {"success": False, "error_code": "connection_unavailable"}
        allowed = {"PATH", "PYTHONPATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                   "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP",
                   "TMPDIR", "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONUNBUFFERED",
                   "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                   "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                   "http_proxy", "https_proxy", "all_proxy", "no_proxy",
                   "FINANCIAL_SERVICE_HOME", "FINANCIAL_SERVICE_NETWORK",
                   "FINANCIAL_SERVICE_BROKERS_ENABLED"}
        allowed.update(profile.get("credentials", {}).values())
        if os.name == "nt":
            folded = {key.casefold() for key in allowed}
            environment = {k: v for k, v in os.environ.items() if k.casefold() in folded}
        else:
            environment = {k: v for k, v in os.environ.items() if k in allowed}
    except Exception:
        return {"success": False, "error_code": "broker_configuration_invalid"}
    if not _QUERY_LOCK.acquire(blocking=False):
        return {"success": False, "error_code": "broker_busy"}
    try:
        request = json.dumps({"connection_id": connection_id, "operation": operation,
                              "status": status, "limit": limit})
        if len(request.encode()) > 1024:
            return {"success": False, "error_code": "invalid_broker_query"}
        result = subprocess.run([sys.executable, "-m", "arthera_finance.brokers.worker"],
                                input=request.encode(), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=30, check=False, env=environment)
        if result.returncode or len(result.stdout) > 1_000_000:
            return {"success": False, "error_code": "broker_worker_failed"}
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or type(data.get("success")) is not bool:
            return {"success": False, "error_code": "broker_worker_failed"}
        return data
    except subprocess.TimeoutExpired:
        return {"success": False, "error_code": "broker_timeout"}
    except Exception:
        return {"success": False, "error_code": "broker_worker_failed"}
    finally:
        _QUERY_LOCK.release()


def main():
    from .service import query
    try:
        raw = sys.stdin.buffer.read(1025)
        if len(raw) > 1024:
            raise ValueError("Request limit exceeded")
        request = json.loads(raw)
        with contextlib.redirect_stdout(sys.stderr):
            result = query(**request)
    except Exception:
        result = {"success": False, "error_code": "invalid_broker_query"}
    sys.stdout.write(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
