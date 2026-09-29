"""Private, rotating error diagnostics; never exposed through API history or SSE."""

import json
import logging
import os
import re
import time
import traceback
import uuid
from logging.handlers import RotatingFileHandler
from urllib.parse import quote, quote_plus

logger = logging.getLogger("kairos.errors")
logger.addHandler(logging.NullHandler())
logger.propagate = False
_secrets = set()


def register_secrets(*values):
    for value in values:
        if isinstance(value, str) and value:
            _secrets.update(
                (value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1])
            )


def redact(text):
    for value in sorted(_secrets, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s\"',;}]+", r"\1 [REDACTED]", text)
    text = re.sub(
        r"(?i)([\"']?(?:api[_-]?key|api[_-]?sign|authent|authorization|secret|password|token|csrf|x-csrf-token|nonce|private[_-]?key)[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
        r"\1[REDACTED]",
        text,
    )
    # Never retain URL credentials or query parameters from transport exception text.
    text = re.sub(r"(https?://)[^/\s]+@", r"\1[REDACTED]@", text)
    return re.sub(r"(https?://[^\s?'\"]+)\?[^\s'\"]*", r"\1?[REDACTED]", text)


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(
            self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")


def configure(path):
    handler = PrivateRotatingHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    return handler


def capture(exc, component):
    """Preserve causes and frame locations, but never locals, headers or request bodies."""
    error_id = str(uuid.uuid4())
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        row = {
            "type": type(current).__name__,
            "message": redact(str(current))[:8192],
            "frames": [
                {"file": f.filename, "line": f.lineno, "function": f.name}
                for f in traceback.extract_tb(current.__traceback__)
            ],
        }
        if hasattr(current, "diagnostic_details"):
            row["details"] = redact(str(current.diagnostic_details))[:8192]
        chain.append(row)
        current = current.__cause__ or current.__context__
    logger.error(
        json.dumps({"id": error_id, "ts": time.time(), "component": component, "chain": chain})
    )
    return error_id
