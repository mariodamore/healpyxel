#!/usr/bin/env python3
"""Convert Python scripts to nbdev notebooks."""

import json
from pathlib import Path
from typing import List, Tuple

def create_notebook_from_script(
    script_path: Path,
    module_name: str,
    title: str,
    description: str
) -> dict:
    """Create a notebook structure from a Python script.
    
    Args:
        script_path: Path to the Python script
        module_name: Name for the module (e.g., 'sidecar')
        title: Notebook title
        description: Short description
        
    Returns:
        Notebook dictionary ready for JSON serialization
    """
    code = script_path.read_text()
    
    # Remove shebang if present
    lines = code.split('\n')
    if lines[0].startswith('#!'):
        lines = lines[1:]
        code = '\n'.join(lines)
    
    notebook = {
        'cells': [],
        'metadata': {
            'kernelspec': {
                'display_name': 'python3',
                'language': 'python',
                'name': 'python3'
            }
        },
        'nbformat': 4,
        'nbformat_minor': 4
    }
    
    # Title cell
    notebook['cells'].append({
        'cell_type': 'markdown',
        'metadata': {},
        'source': [f'# {title}\n', '\n', f'> {description}']
    })
    
    # default_exp directive
    notebook['cells'].append({
        'cell_type': 'code',
        'execution_count': None,
        'metadata': {},
        'outputs': [],
        'source': [f'#| default_exp {module_name}']
    })
    
    # Export the code (skip if __name__ == "__main__" block)
    code_lines = code.split('\n')
    export_lines = []
    in_main_block = False
    
    for i, line in enumerate(code_lines):
        if 'if __name__ == "__main__"' in line:
            in_main_block = True
            # Keep the main function definition but not the execution
            export_lines.append('')
            export_lines.append('# CLI entry point (use via command line or import main() function)')
            break
        if not in_main_block:
            export_lines.append(line)
    
    # Properly format source lines with newlines
    formatted_lines = ['#| export\n'] + [line + '\n' for line in export_lines]
    
    notebook['cells'].append({
        'cell_type': 'code',
        'execution_count': None,
        'metadata': {},
        'outputs': [],
        'source': formatted_lines
    })
    
    # Add usage example cell
    notebook['cells'].append({
        'cell_type': 'markdown',
        'metadata': {},
        'source': ['## Usage Example\n', '\n', 'See the `main()` function for CLI usage, or import functions directly for programmatic use.']
    })
    
    return notebook


def main():
    """Convert all scripts to notebooks."""
    scripts = [
        ('healpix_sidecar.py', 'sidecar', '01_sidecar.ipynb', 
         'HEALPix Sidecar', 'Generate HEALPix cell assignments for spatial data'),
        ('healpix_aggregate.py', 'aggregate', '02_aggregate.ipynb',
         'HEALPix Aggregate', 'Aggregate data by HEALPix cells (batch processing)'),
        ('healpix_accumulator.py', 'accumulator', '03_accumulator.ipynb',
         'HEALPix Accumulator', 'Streaming accumulation with incremental statistics'),
        ('healpix_finalize.py', 'finalize', '04_finalize.ipynb',
         'HEALPix Finalize', 'Convert accumulator state to final statistics'),
    ]
    
    base_path = Path('_scripts_original')
    nbs_path = Path('nbs')
    
    for script_name, module_name, notebook_name, title, description in scripts:
        script_path = base_path / script_name
        if not script_path.exists():
            print(f'Warning: {script_path} not found, skipping')
            continue
            
        notebook = create_notebook_from_script(
            script_path, module_name, title, description
        )
        
        output_path = nbs_path / notebook_name
        output_path.write_text(json.dumps(notebook, indent=1))
        print(f'✓ Created {notebook_name}')


if __name__ == '__main__':
    main()
