#!/usr/bin/env python3
"""Launch Friday's Quiet IDE without a voice stack or API key.

    python launch_ide.py
    python launch_ide.py /path/to/project

For Friday's real agent integration, launch agent_overlay.py from this
workspace instead. This entry point intentionally does not start a fake
agent or make a model request; chat explains that no agent is connected.
"""
from pathlib import Path
import argparse
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'ide'))


def main():
    parser=argparse.ArgumentParser(description='Friday IDE — a quieter place to build')
    parser.add_argument('folder',nargs='?',help='Existing project folder to open')
    parser.add_argument('--no-chat',action='store_true',help='Start with the chat panel hidden')
    args=parser.parse_args()
    if args.folder:
        folder=Path(args.folder).expanduser().resolve()
        if not folder.is_dir():parser.error('The project folder does not exist: '+str(folder))
    else:
        folder=Path(tempfile.mkdtemp(prefix='friday-empty-'))
    try:
        from ide import IDEApp
    except ImportError as error:
        print('Install the IDE GUI dependencies first:\n'
              '    python -m pip install -r requirements-ide.txt\n\n'+str(error),file=sys.stderr)
        return 1
    app=IDEApp(str(folder),show_chat=not args.no_chat)
    app.mainloop()
    # Empty temporary scratch folders can go away; preserve anything
    # actually created there (the editor worked on real files).
    if not args.folder:
        try:folder.rmdir()
        except OSError:pass
    return 0


if __name__=='__main__':
    raise SystemExit(main())
