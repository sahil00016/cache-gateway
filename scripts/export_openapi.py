"""Export the OpenAPI schema to a file.

CI runs this and diffs the result against the committed api/openapi.json. A
mismatch fails the build, so the spec cannot silently drift from the code --
which matters because the dashboard's TypeScript client is generated from it.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    """Write the schema to the path given as the first argument.

    Returns:
        Process exit code.
    """
    if len(sys.argv) != 2:  # noqa: PLR2004 -- script name plus one argument
        sys.stderr.write("usage: python -m scripts.export_openapi <output-path>\n")
        return 2

    # Imported lazily and deliberately: importing src.main builds the app,
    # which validates settings. Doing that before the argument check would
    # turn a usage error into a confusing configuration error.
    from src.main import create_app  # noqa: PLC0415

    schema = create_app().openapi()
    destination = Path(sys.argv[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so the diff is stable across runs and Python versions.
    destination.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
