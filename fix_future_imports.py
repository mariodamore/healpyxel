#!/usr/bin/env python3
"""Fix from __future__ import position in generated modules."""

import re
from pathlib import Path

def fix_future_import(filepath: Path):
    """Move from __future__ import to the top of the file."""
    content = filepath.read_text()
    
    # Find the from __future__ import line
    future_match = re.search(r'^from __future__ import.*$', content, re.MULTILINE)
    if not future_match:
        print(f'{filepath.name}: No future import found')
        return
    
    future_line = future_match.group(0)
    
    # Remove it from current position
    content = content.replace(future_line + '\n', '', 1)
    
    # Split into lines and find insertion point
    lines = content.split('\n')
    insert_pos = 0
    
    # Skip shebang if present
    if lines[0].startswith('#!'):
        insert_pos = 1
    
    # Insert at top
    lines.insert(insert_pos, future_line)
    
    # Write back
    filepath.write_text('\n'.join(lines))
    print(f'✓ Fixed {filepath.name}')

if __name__ == '__main__':
    module_dir = Path('healpyxel')
    for module_file in ['sidecar.py', 'aggregate.py', 'accumulator.py', 'finalize.py']:
        filepath = module_dir / module_file
        if filepath.exists():
            fix_future_import(filepath)
