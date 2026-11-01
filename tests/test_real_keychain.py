"""A key through this machine's own keychain, written by one process and read by another.

Every other test keeps off the system keychain (``tests/conftest.py``), so none writes to
a developer's own. This one asks for it, and runs only where ``BAILEY_TEST_REAL_KEYCHAIN``
is ``1``: CI sets it on the macOS and Windows runners, whose keychains are the ones most
users have. A machine without a keychain skips it, as Linux CI does; the encrypted file
it falls back to is tested everywhere.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys

import keyring
import keyring.core
import pytest
from running import SRC

from bailey.keys import KeychainKeys, keychain_backend

pytestmark = pytest.mark.skipif(
    os.environ.get("BAILEY_TEST_REAL_KEYCHAIN") != "1",
    reason="writes to this machine's keychain; set BAILEY_TEST_REAL_KEYCHAIN=1 to run it",
)

_READ_BACK = """
import sys
from bailey.keys import open_keys
from pathlib import Path
keys = open_keys(Path(sys.argv[2]), environ={})
print(keys.kind, keys.get(sys.argv[1]) or "")
"""


def test_a_key_saved_in_the_keychain_is_read_back_by_another_process(tmp_path: object) -> None:
    keyring.core.init_backend()  # the real detection, in place of the conftest's stand-in
    backend = keychain_backend(keyring.get_keyring())
    if backend is None:
        pytest.skip("this machine has no keychain Bailey would use")
    keys = KeychainKeys(backend)
    name = f"test/{secrets.token_hex(8)}"
    value = f"sk-test-{secrets.token_hex(12)}"
    keys.set(name, value)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _READ_BACK, name, str(tmp_path)],
            env={**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.split() == ["keychain", value]
    finally:
        keys.delete(name)
    assert keys.get(name) is None
