"""Guards that importing application modules has no side effects.

This exists because of a real CI failure. ``src/main.py`` used to end with
``app = create_app()``, so merely importing it validated settings. That passed
locally, where a .env file exists, and failed in CI, where none does.

The rule these tests enforce: the factory module is importable with no
configuration whatsoever; only ``src.asgi``, which servers bind to, requires
a valid environment.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_with_empty_config(code: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet with no config env vars and no .env discoverable.

    Runs from a temp-free directory so the developer's .env is not found, and
    strips every setting from the environment, which is what CI looks like.

    Args:
        code: Python source to execute.

    Returns:
        The completed process.
    """
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(PROJECT_ROOT),
        # Deliberately absent: DATABASE_URL, REDIS_URL and every other setting.
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd="/",  # so no .env in the project root is discovered
    )


def test_main_imports_without_any_configuration() -> None:
    result = _run_with_empty_config("import src.main")

    assert result.returncode == 0, (
        f"src.main must be importable with no configuration. stderr:\n{result.stderr}"
    )


def test_main_exposes_no_module_level_app() -> None:
    # The presence of a module-level `app` is what caused the original failure.
    result = _run_with_empty_config(
        "import src.main; assert not hasattr(src.main, 'app'), 'src.main.app exists'"
    )

    assert result.returncode == 0, result.stderr


def test_asgi_still_fails_fast_without_configuration() -> None:
    # The fix must not weaken boot validation: the entrypoint servers bind to
    # must still refuse to start when misconfigured.
    result = _run_with_empty_config("import src.asgi")

    assert result.returncode != 0
    assert "database_url" in result.stderr.lower()


def test_openapi_export_needs_no_database() -> None:
    # Generating an API contract must never require a database to exist.
    result = _run_with_empty_config(
        "import sys; sys.argv=['x','/tmp/spec-test.json'];"
        "from scripts.export_openapi import main; sys.exit(main())"
    )

    assert result.returncode == 0, result.stderr
