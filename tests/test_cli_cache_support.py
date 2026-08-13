"""Test support for cache_cli Click command tests.

cache_cli() constructs a click.command, calls it with sys.argv, and
returns. It also calls sys.exit(0) via click.echo at the end (Click 8.x).

We test it by:
1. Patching healpyxel.geospatial.manage_healpix_cache (the inner import)
2. Patching sys.argv to inject test arguments
3. Calling cache_cli() directly and capturing stdout
"""
from unittest.mock import patch, MagicMock

import sys
import io
from contextlib import redirect_stdout, redirect_stderr


def run_cache_cli(args, mock_manage=None):
    """Run cache_cli with given CLI args and return (stdout_str, stderr_str, exit_code).

    Patches all the necessary internals so cache_cli can run in a test environment.
    """
    import healpyxel.geospatial as geo_mod
    old_manage = geo_mod.manage_healpix_cache

    if mock_manage is None:
        mock_manage = lambda **kw: kw

    geo_mod.manage_healpix_cache = mock_manage

    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0

    sys.argv = ['healpyxel-cache'] + args

    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        from healpyxel.cli import cache_cli
        cache_cli()
    except SystemExit as e:
        exit_code = e.code or 0
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        geo_mod.manage_healpix_cache = old_manage

    return stdout_buf.getvalue(), stderr_buf.getvalue(), exit_code
