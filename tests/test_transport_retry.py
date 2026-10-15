"""Temporary-network retry for the Claude Code updater. No native install."""

import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))

import bounded  # noqa: E402
import entry  # noqa: E402

SECRET = "SENTINEL_SECRET_http://user:password@evil.example/secret"
SHA = "b" * 40
CURRENT = "a" * 40


def _script(body):
    return "import sys\n" + body


NET = _script(
    f"sys.stdout.write({SECRET!r} + '\\n')\n"
    "sys.stdout.write('ERROR: Could not fetch URL\\n')\n"
    "sys.stdout.write('NewConnectionError: connection reset by peer\\n')\n"
    f"sys.stderr.write({SECRET!r} + '\\n')\n"
    "raise SystemExit(1)\n"
)
RATE = _script(
    "sys.stderr.write('The requested URL returned error: 403\\n')\n"
    "sys.stderr.write('secondary rate limit\\nRetry-After: 12\\n')\n"
    f"sys.stderr.write({SECRET!r} + '\\n')\n"
    "raise SystemExit(128)\n"
)
AUTH = _script(
    f"sys.stderr.write('Authentication failed for {SECRET}\\n')\n"
    "sys.stderr.write('The requested URL returned error: 403\\n')\n"
    "raise SystemExit(128)\n"
)
CERT = _script(
    "sys.stderr.write('SSL: CERTIFICATE_VERIFY_FAILED\\n')\n"
    f"sys.stderr.write({SECRET!r} + '\\n')\n"
    "raise SystemExit(1)\n"
)
HANG = _script(
    "import time\n"
    f"sys.stderr.write({SECRET!r} + '\\n')\n"
    "sys.stderr.flush()\n"
    "time.sleep(30)\n"
)


def _public(exc):
    return json.dumps({
        "error": str(exc),
        "category": getattr(exc, "category", None),
        "retry_after": getattr(exc, "retry_after", None),
    })


def _run(body, *, transport, timeout=5):
    return bounded.run(
        [sys.executable, "-c", body], "", timeout=timeout, transport=transport
    )


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


class ClassificationTests(unittest.TestCase):
    def assertClean(self, exc):
        blob = _public(exc)
        self.assertNotIn("SENTINEL", blob)
        self.assertNotIn("password", blob)
        self.assertNotIn("http://", blob)

    def test_subprocess_classification(self):
        with self.assertRaises(RuntimeError) as caught:
            _run(NET, transport=True)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "temporary_network")

        with self.assertRaises(RuntimeError) as caught:
            _run(RATE, transport=True)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "rate_limited")
        self.assertEqual(caught.exception.retry_after, 12.0)

        with self.assertRaises(RuntimeError) as caught:
            _run(AUTH, transport=True)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "authentication")

        with self.assertRaises(RuntimeError) as caught:
            _run(CERT, transport=True)
        self.assertEqual(caught.exception.category, "certificate")

        with self.assertRaises(RuntimeError) as caught:
            _run(NET, transport=False)
        self.assertIsNone(getattr(caught.exception, "category", None))
        with self.assertRaises(RuntimeError) as caught:
            _run(
                _script(
                    "sys.stderr.write(\"fatal: unable to access 'https://example.invalid/r.git/': "
                    "Proxy CONNECT aborted\\n\")\nraise SystemExit(128)\n"
                ),
                transport=True,
            )
        self.assertEqual(caught.exception.category, "temporary_network")
        with self.assertRaises(RuntimeError) as caught:
            _run(
                _script(
                    "sys.stdout.write('ERROR: NewConnectionError: connection refused\\n')\n"
                    "sys.stdout.write('ERROR: Could not find a version that satisfies the requirement example\\n')\n"
                    "sys.stdout.write('ERROR: No matching distribution found for example\\n')\n"
                    "raise SystemExit(1)\n"
                ),
                transport=True,
            )
        self.assertEqual(caught.exception.category, "temporary_network")
        with self.assertRaises(RuntimeError) as caught:
            _run(
                _script(
                    "sys.stdout.write('A' * 70000)\n"
                    "sys.stderr.write('fatal: failed to connect to server\\n')\n"
                    "raise SystemExit(128)\n"
                ),
                transport=True,
            )
        self.assertEqual(caught.exception.category, "temporary_network")

    def test_timeout_classification_follows_the_phase(self):
        with self.assertRaises(bounded.CommandTimedOut) as caught:
            _run(HANG, transport=True, timeout=0.4)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "temporary_network")
        with self.assertRaises(bounded.CommandTimedOut) as plain:
            _run(HANG, transport=False, timeout=0.4)
        self.assertIsNone(plain.exception.category)
