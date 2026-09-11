"""Restrict the local shutdown token to the service account."""

import csv
import os
import subprocess
from pathlib import Path


def private_file(path: Path) -> None:
    """Remove inherited read access using native platform permissions.

    :param path: Existing control-token file owned by the service account.
    :raises OSError: If permission changes or Windows tools fail to start.
    :raises subprocess.CalledProcessError: If Windows ACL adjustment fails.
    """
    if os.name == "nt":
        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
        )
        sid = next(csv.reader(identity.stdout.splitlines()))[1]
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
            check=True,
            capture_output=True,
        )
    else:
        path.chmod(0o600)
