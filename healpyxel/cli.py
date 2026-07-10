"""CLI entry points for healpyxel commands.

All argparse definitions live here. Submodules expose pure run(config) APIs.
"""

import sys


def sidecar_cli(argv=None):
    """CLI entry point for healpyxel_sidecar."""
    from healpyxel.sidecar import parse_arguments, run
    args = parse_arguments(argv)
    return run(args)


def aggregate_cli(argv=None):
    """CLI entry point for healpyxel_aggregate."""
    from healpyxel.aggregate import parse_arguments, run
    args = parse_arguments(argv)
    return run(args)


def accumulator_cli(argv=None):
    """CLI entry point for healpyxel_accumulator."""
    from healpyxel.accumulator import parse_arguments, run
    args = parse_arguments(argv)
    return run(args)


def finalize_cli(argv=None):
    """CLI entry point for healpyxel_finalize."""
    from healpyxel.finalize import parse_arguments, run
    args = parse_arguments(argv)
    return run(args)


def to_geoparquet_cli(argv=None):
    """CLI entry point for healpyxel_to_geoparquet."""
    from healpyxel.geospatial import parse_arguments, run
    args = parse_arguments(argv)
    return run(args)


# %% ../nbs/05_cli.ipynb #a77cb0a9
def validate_lon_lat_columns(df, lon_col, lat_col, mode, input_file):
    """
    Validate or auto-detect lon/lat columns. Fail fast with context.
    Returns (lon_col, lat_col) or raises ValueError.
    """
    import logging
    logger = logging.getLogger(__name__)

    # User provided both—just validate they exist
    if lon_col and lat_col:
        if lon_col not in df.columns or lat_col not in df.columns:
            raise ValueError(
                f"Specified columns not found in {input_file}:\n"
                f"  --lon-col {lon_col}: {lon_col in df.columns}\n"
                f"  --lat-col {lat_col}: {lat_col in df.columns}\n"
                f"  Available: {list(df.columns)}"
            )
        logger.info(f"Using user columns: lon={lon_col}, lat={lat_col}")
        return lon_col, lat_col

    # Auto-detect from common names
    lon_candidates = ["lon", "longitude", "spot_lon", "x"]
    lat_candidates = ["lat", "latitude", "spot_lat", "y"]

    detected_lon = next((c for c in lon_candidates if c in df.columns), None)
    detected_lat = next((c for c in lat_candidates if c in df.columns), None)

    if detected_lon and detected_lat:
        logger.info(f"Auto-detected: lon={detected_lon}, lat={detected_lat}")
        return detected_lon, detected_lat

    # Auto-detect failed—provide actionable error
    raise ValueError(
        f"[{mode.upper()} MODE] Could not auto-detect lon/lat columns.\n"
        f"  Checked for: {lon_candidates} / {lat_candidates}\n"
        f"  Found: lon={detected_lon}, lat={detected_lat}\n"
        f"  Available columns: {list(df.columns)}\n"
        f"  Fix: Specify --lon-col and --lat-col, or rename columns to match "
        f"one of the checked names."
    )


# %% ../nbs/05_cli.ipynb #5c802a26
def cache_cli():
    """CLI entry point for healpyxel-cache command."""
    import click
    import os
    from healpyxel.geospatial import manage_healpix_cache

    @click.command('healpyxel-cache', context_settings={'help_option_names': ['-h', '--help']})
    @click.option('--list', 'action_list', is_flag=True, help='List cached grids')
    @click.option('--generate', type=int, multiple=True, help='Generate cache for specific nsides (e.g., --generate 32 --generate 256)')
    @click.option('--verify', type=int, multiple=True, help='Verify cache integrity for specific nsides (e.g., --verify 256 --verify 512)')
    @click.option('--clean', is_flag=True, help='Remove all cache files')
    @click.option('--info', is_flag=True, help='Show cache directory info')
    @click.option('--config', is_flag=True, help='Show configuration and precedence')
    @click.option('--cache-dir', type=click.Path(), default=None,
                  help='Override cache directory. Precedence: CLI > HEALPYXEL_CACHE > $XDG_CACHE_HOME/healpyxel/healpix_grids > $HOME/.cache/healpyxel/healpix_grids')
    @click.option('--config-dir', type=click.Path(), default=None,
                  help='Override config directory. Precedence: CLI > HEALPYXEL_CONFIG > $XDG_CONFIG_HOME/healpyxel > $HOME/.config/healpyxel')
    @click.option('--force', is_flag=True, help='Overwrite existing cache files during --generate')
    @click.option('--quiet', is_flag=True, help='Minimal output (JSON)')
    def cmd(action_list, generate, verify, clean, info, config, cache_dir, config_dir, force, quiet):
        """Manage HEALPix grid cache with XDG compliance."""
        import json
        from pathlib import Path

        # Determine action
        if action_list:
            action = 'list'
        elif generate:
            action = 'generate'
        elif verify:
            action = 'verify'
        elif clean:
            action = 'clean'
        elif info:
            action = 'info'
        elif config:
            action = 'config'
        else:
            click.echo('No action specified. Use --list, --generate, --verify, --clean, --info, or --config.')
            click.echo('Run with -h for help and precedence details.')
            return

        # Dispatch to domain logic
        try:
            result = manage_healpix_cache(
                action=action,
                nsides=list(generate) if generate else (list(verify) if verify else None),
                cache_dir=Path(cache_dir) if cache_dir else None,
                config_dir=Path(config_dir) if config_dir else None,
                force=force
            )
        except Exception as e:
            click.echo(f'Error: {e}', err=True)
            raise click.Abort()

        # Format output
        if quiet:
            click.echo(json.dumps(result, default=str, indent=2))
        else:
            if action == 'list':
                click.echo(f"Cache directory: {result['cache_dir']}")
                if not result['files']:
                    click.echo('No cached grids found.')
                else:
                    click.echo(f"Cached grids ({result['count']}):")
                    for f in result['files']:
                        click.echo(f"  {f['filename']:45s} {f['cells']:6d} cells  {f['size_mb']:7.1f} MB")

            elif action == 'verify':
                has_errors = result['status'] == 'error'
                for ver in result['verified']:
                    if ver['status'] == 'ok':
                        click.echo(f"nside={ver['nside']:3d}  OK ({ver['cells']:6d} cells, {ver['size_mb']:7.1f} MB)")
                    elif ver['status'] == 'missing':
                        click.echo(f"nside={ver['nside']:3d}  MISSING: {ver['error']}", err=True)
                    elif ver['status'] == 'incomplete':
                        click.echo(f"nside={ver['nside']:3d}  INCOMPLETE: {ver['error']} ({ver.get('missing_count', 0)} missing)", err=True)
                    elif ver['status'] == 'corrupt':
                        click.echo(f"nside={ver['nside']:3d}  CORRUPT: {ver['error']}", err=True)
                    else:
                        click.echo(f"nside={ver['nside']:3d}  ERROR: {ver.get('error', 'unknown error')}", err=True)

                if has_errors:
                    raise click.Abort()

            elif action == 'config':
                click.echo(f"Config file: {result['config_file']}")
                click.echo(f"Exists: {result['config_exists']}")
                click.echo()
                click.echo('Current Settings:')
                for key, val in result['settings'].items():
                    click.echo(f"  {key:25s} {val}")
                click.echo()
                click.echo('Precedence Resolution:')
                for key, val in result['precedence'].items():
                    click.echo(f"  {key:25s} {val}")

            elif action == 'generate':
                for gen in result['generated']:
                    status_icon = '+' if gen['status'] == 'ok' else ('o' if gen['status'] == 'skipped' else 'x')
                    msg = f"{status_icon} nside={gen['nside']:3d}"
                    if gen['status'] == 'ok':
                        msg += f"  {gen['cells']:6d} cells"
                    elif gen['status'] == 'skipped':
                        msg += f"  {gen['reason']}"
                    else:
                        msg += f"  ERROR: {gen['error']}"
                    click.echo(msg)

            elif action == 'clean':
                if result['deleted'] > 0:
                    click.echo(f"Deleted {result['deleted']} cache file(s) from {result['cache_dir']}")
                else:
                    click.echo('No cache files to delete.')

            elif action == 'info':
                click.echo(f"Cache directory: {result['cache_dir']}")
                click.echo(f"  Exists: {result['cache_dir_exists']}")
                click.echo(f"  Files: {result['total_files']}")
                click.echo(f"  Total size: {result['total_size_mb']:.1f} MB")
                click.echo()
                click.echo(f"Config directory: {result['config_dir']}")
                click.echo(f"  Exists: {result['config_dir_exists']}")

    return cmd()
