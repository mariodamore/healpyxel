import json

from tests.test_cli_cache_support import run_cache_cli


class TestCacheCli:
    """Test cache_cli Click command by calling it directly with patched internals.

    cache_cli() builds a Click command and calls it with sys.argv, and Click 8.x
    calls sys.exit(0) on clean completion. We test it by:
    1. Patching healpyxel.geospatial.manage_healpix_cache (the inner import)
    2. Patching sys.argv to inject test arguments
    3. Calling cache_cli() directly and capturing stdout
    """

    def _run(self, args, mock_manage):
        return run_cache_cli(args, mock_manage)

    def test_list_action(self):
        mock_manage = lambda **kw: {
            'action': 'list', 'cache_dir': '/tmp/cache',
            'files': [{'filename': 'nside_004_nest.parquet', 'cells': 768, 'size_mb': 0.1}],
            'count': 1, 'status': 'ok',
        }
        stdout, stderr, exit_code = self._run(['--list'], mock_manage)
        assert '/tmp/cache' in stdout
        assert 'nside_004_nest.parquet' in stdout

    def test_list_empty_cache(self):
        mock_manage = lambda **kw: {
            'action': 'list', 'cache_dir': '/tmp/cache',
            'files': [], 'count': 0, 'status': 'ok',
        }
        stdout, stderr, exit_code = self._run(['--list'], mock_manage)
        assert 'No cached grids found' in stdout

    def test_generate_action(self):
        mock_manage = lambda **kw: {
            'action': 'generate', 'status': 'ok',
            'generated': [{'nside': 32, 'status': 'ok', 'cells': 12288}],
        }
        stdout, stderr, exit_code = self._run(['--generate', '32'], mock_manage)
        assert '+ nside= 32' in stdout

    def test_generate_skipped(self):
        mock_manage = lambda **kw: {
            'action': 'generate', 'status': 'ok',
            'generated': [
                {'nside': 32, 'status': 'skipped', 'reason': 'already exists'},
            ],
        }
        stdout, stderr, exit_code = self._run(['--generate', '32'], mock_manage)
        assert 'o nside= 32' in stdout

    def test_verify_ok(self):
        mock_manage = lambda **kw: {
            'action': 'verify', 'status': 'ok',
            'verified': [{'nside': 32, 'status': 'ok', 'cells': 12288, 'size_mb': 0.1}],
        }
        stdout, stderr, exit_code = self._run(['--verify', '32'], mock_manage)
        assert 'nside= 32  OK' in stdout

    def test_verify_missing(self):
        mock_manage = lambda **kw: {
            'action': 'verify', 'status': 'error', 'verified': [
                {'nside': 32, 'status': 'missing', 'error': 'Cache file not found'},
            ],
        }
        stdout, stderr, exit_code = self._run(['--verify', '32'], mock_manage)
        assert 'MISSING' in (stdout + stderr)
        # click.Abort() calls sys.exit(1) via sys.exit(raise SystemExit(1))
        assert exit_code != 0

    def test_clean_action(self):
        mock_manage = lambda **kw: {
            'action': 'clean', 'status': 'ok', 'deleted': 3,
            'cache_dir': '/tmp/cache',
        }
        stdout, stderr, exit_code = self._run(['--clean'], mock_manage)
        assert 'Deleted 3 cache file(s)' in stdout

    def test_clean_empty(self):
        mock_manage = lambda **kw: {
            'action': 'clean', 'status': 'ok', 'deleted': 0,
            'cache_dir': '/tmp/cache',
        }
        stdout, stderr, exit_code = self._run(['--clean'], mock_manage)
        assert 'No cache files to delete' in stdout

    def test_info_action(self):
        mock_manage = lambda **kw: {
            'action': 'info', 'status': 'ok',
            'total_files': 5, 'total_size_mb': 12.3,
            'cache_dir': '/tmp/cache', 'cache_dir_exists': True,
            'config_dir': '/tmp/config', 'config_dir_exists': True,
        }
        stdout, stderr, exit_code = self._run(['--info'], mock_manage)
        assert 'Files: 5' in stdout
        assert 'Total size: 12.3 MB' in stdout

    def test_config_action(self):
        mock_manage = lambda **kw: {
            'action': 'config', 'status': 'ok',
            'config_file': '/tmp/config/settings.ini', 'config_exists': True,
            'settings': {'cache_dir': 'auto', 'precomputed_nsides': [32, 64]},
            'precedence': {'cache_dir_resolved': '/tmp/cache'},
        }
        stdout, stderr, exit_code = self._run(['--config'], mock_manage)
        assert 'Config file:' in stdout
        assert 'Current Settings:' in stdout
        assert 'Precedence Resolution:' in stdout

    def test_quiet_output(self):
        mock_manage = lambda **kw: {
            'action': 'list', 'status': 'ok', 'files': [], 'count': 0,
        }
        stdout, stderr, exit_code = self._run(['--list', '--quiet'], mock_manage)
        data = json.loads(stdout)
        assert data['status'] == 'ok'
        assert data['count'] == 0

    def test_no_action_message(self):
        mock_manage = lambda **kw: {'action': 'list', 'status': 'ok', 'files': [], 'count': 0}
        stdout, stderr, exit_code = self._run([], mock_manage)
        assert 'No action specified' in (stdout + stderr)

    def test_help_flag(self):
        mock_manage = lambda **kw: kw
        stdout, stderr, exit_code = self._run(['--help'], mock_manage)
        assert 'Manage HEALPix grid cache' in stdout

    def test_exception_in_manage_healpix_cache_aborts(self):
        """When manage_healpix_cache raises, the error is printed and the process aborts."""
        def failing_manage(**kw):
            raise RuntimeError("Something went wrong in geospatial")

        stdout, stderr, exit_code = self._run(['--list'], failing_manage)
        assert exit_code != 0
        assert 'Error' in (stdout + stderr) or 'Aborted' in (stdout + stderr)
