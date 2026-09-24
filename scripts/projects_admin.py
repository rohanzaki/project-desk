"""Human-run admin commands for Project Desk projects.

move         Move a repo's records between projects (dry run unless --apply).
audit        Recently seen sessions registered in a different project than their worktree declares.
set-project  Set a project's name, repo folders or rules file. --repo-root replaces the whole list.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from store import Store  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', default=str(ROOT / 'data/desk.sqlite3'))
    sub = parser.add_subparsers(dest='command', required=True)
    move = sub.add_parser('move')
    move.add_argument('--from', dest='source', required=True)
    move.add_argument('--to', dest='target', required=True)
    move.add_argument('--worktree-prefix', action='append', required=True)
    move.add_argument('--idle-hours', type=float, default=6)
    move.add_argument('--export', default='')
    move.add_argument('--apply', action='store_true')
    audit = sub.add_parser('audit')
    audit.add_argument('--hours', type=float, default=6)
    settings = sub.add_parser('set-project')
    settings.add_argument('slug')
    settings.add_argument('--name')
    settings.add_argument('--repo-root', action='append')
    settings.add_argument('--rules-path')
    args = parser.parse_args(argv)
    store = Store(args.db)
    if args.command == 'move':
        result = store.move_project(args.source, args.target, args.worktree_prefix,
                                    args.apply, args.idle_hours, args.export)
    elif args.command == 'audit':
        result = store.session_mismatches(args.hours)
    else:
        result = store.update_project(args.slug, name=args.name, repo_roots=args.repo_root,
                                      rules_path=args.rules_path)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
