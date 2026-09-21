"""Fail if the migration history has more than one head.

Two heads mean two branches were merged without reconciling their migrations.
Alembic will not tell you until someone runs `upgrade head` and it fails --
usually in a deploy. Catching it in CI is much cheaper.
"""

import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> int:
    """Check the number of Alembic heads.

    Returns:
        Process exit code: 0 for one or zero heads, 1 otherwise.
    """
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()

    if len(heads) > 1:
        sys.stderr.write(f"Multiple Alembic heads found: {heads}\n")
        sys.stderr.write(
            "Merge them with: alembic merge -m 'merge heads' " + " ".join(heads) + "\n"
        )
        return 1

    sys.stdout.write(f"Alembic heads OK: {heads or ['(none yet)']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
