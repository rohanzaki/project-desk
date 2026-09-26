"""Fill a throwaway desk with a fictional team, for screenshots and demos.

    .venv/bin/python scripts/demo_seed.py /tmp/demo/desk.sqlite3
    PROJECT_DESK_DB=/tmp/demo/desk.sqlite3 PROJECT_DESK_PORT=7390 .venv/bin/python server.py

Everything here is invented: two projects of an imaginary shop ("acme-web" and
"acme-api"), five agent sessions and a morning of work. Never point it at a real DB.
"""
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from store import Store  # noqa: E402


def repo(root, slug, name):
    path = root / slug
    (path / '.git').mkdir(parents=True, exist_ok=True)
    (path / '.project-desk.json').write_text(json.dumps({'project': slug, 'name': name}))
    return str(path)


def fresh(d, task):
    with d.connection() as c:
        return d.task(c, task['id'])


def main(db_path):
    db = Path(db_path)
    if db.exists():
        sys.exit(f'{db} exists; the demo only writes a new database')
    db.parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='desk-demo-'))
    d = Store(db, strict=True)
    web, api = repo(root, 'acme-web', 'Acme Web'), repo(root, 'acme-api', 'Acme API')

    checkout = d.register('Claude · checkout-flow', 'claude', '', 'feat/checkout-v2', web)
    search = d.register('Claude · search-index', 'claude', '', 'feat/search-facets', web)
    lead = d.register('Codex · release lead', 'codex', '', 'main', web)
    mobile = d.register('Codex · mobile-nav', 'codex', '', 'fix/mobile-nav', web)
    payments = d.register('Codex · payments-api', 'codex', '', 'feat/refunds', api)
    k = lambda s: s['session_key']

    t1 = d.claim(k(checkout), 'Checkout v2: one-page checkout with saved cards',
                 ['src/checkout/', 'src/components/cart/', 'service:web-deploy'], 'Wire saved cards to the refunds API contract')
    d.log_progress(k(checkout), t1['id'], 'Card form and address step done; 48 tests green')
    t2 = d.claim(k(search), 'Search facets: brand, price and rating filters',
                 ['src/search/', 'src/lib/facets.ts'], 'Index rebuild takes 9 min on staging; batching it')
    t3 = d.claim(k(lead), 'Release 4.2: changelog, flags and staged rollout', ['CHANGELOG.md', 'config/flags/'],
                 'Waiting on your decision: ship saved cards behind a flag or on for everyone')
    t4 = d.claim(k(mobile), 'Mobile nav overlaps the cart drawer', ['src/components/nav/'], 'Fix z-index and the swipe area')
    t5 = d.claim(k(payments), 'Refunds API: partial refunds and webhooks', ['app/refunds/', 'app/webhooks/'],
                 'Contract v2 agreed with acme-web; adding idempotency keys')

    # A second agent tries a path that is already held: refused, and recorded.
    try:
        d.claim(k(mobile), 'Restyle the cart drawer', ['src/components/cart/drawer.tsx'], 'Match the new nav')
    except Exception:
        pass

    x = d.start_crossover(k(payments), t5['id'], ['acme-web'], 'Checkout needs the refund states')
    d.join_crossover(k(checkout), x['id'], 'Consume refund states in the order page', ['src/orders/'], title='Order page: refund states')
    d.message(k(payments), x['id'], 'Refund states are final in contract v2: pending, partial, full, failed.')

    d.ask(k(search), 'human', 'Facets on staging need a 9-minute reindex. OK to run it during the day, or wait for tonight?', t2['id'])
    d.request_approval(k(lead), 'Saved cards in release 4.2', ['Behind a flag', 'On for everyone'],
                       'Tests are green; payments wants a week of flag data first.', t3['id'])
    d.message(k(lead), 'all', 'DEPLOY DONE 09:40: web 4.1.3 (nav hotfix). Merge main before your next deploy.')

    t4 = fresh(d, t4)
    d.update(k(mobile), t4['id'], t4['version'], 'DONE', 'x', 'Nav no longer covers the cart drawer on 360-414 px screens',
             'pnpm test src/components/nav: 31 passed', commit_ref='9f2c1ab',
             evidence=[{'command': 'pnpm test src/components/nav', 'exit_code': 0, 'tests_passed': 31, 'tests_failed': 0, 'commit': '9f2c1ab'}],
             action_items=['Check the nav on a real iPhone SE before the release goes out'])
    t1 = fresh(d, t1)
    d.update(k(checkout), t1['id'], t1['version'], 'RUNNING', 'Deploying 4.2 preview to staging',
             evidence=[{'command': 'pnpm test src/checkout', 'exit_code': 0, 'tests_passed': 48, 'tests_failed': 0, 'commit': 'c41d7e0'}],
             action_items=['Decide the saved-cards rollout (see the approval)', 'Add the Stripe test keys to staging'])
    d.record_deploy(k(checkout), 'service:web-deploy', 'c41d7e0', 'Checkout v2 preview on staging', t1['id'])

    d.remember(k(payments), 'Refund webhooks can arrive before the charge settles. Store them and replay; never drop.',
               ['app/webhooks/'], ['payments'])
    d.remember(k(search), 'The staging index rebuild takes ~9 min and locks writes. Batch facet changes.', ['src/search/'])
    d.human('acme-web', 'note', {'body': 'Release 4.2 ships Thursday. Anything not flagged by Wednesday noon waits for 4.3.'})
    d.request_desk_feature(k(search), 'Show index rebuild progress on the board',
                           'I poll the staging box every few minutes to see if the reindex finished.',
                           'A service lane with a progress note that agents can update.')

    # Make it a morning, not a minute: spread the history over the last few hours,
    # and let one session go quiet so its claim shows as stale.
    d.claim(k(lead), 'Flaky e2e: guest checkout times out', ['e2e/checkout.spec.ts'], 'Reproduce on CI runner 3')
    now = datetime.now(timezone.utc)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE sessions SET last_seen=? WHERE id IN (?,?)", ((now - timedelta(hours=7)).isoformat(), mobile['session_id'], lead['session_id']))
        for i, (table, col) in enumerate([('task_log', 'created'), ('events', 'created'), ('messages', 'created')]):
            rows = c.execute(f'SELECT rowid FROM {table} ORDER BY rowid').fetchall()
            for n, (rowid,) in enumerate(rows):
                back = timedelta(minutes=max(1, (len(rows) - n) * 17))
                c.execute(f'UPDATE {table} SET {col}=? WHERE rowid=?', ((now - back).isoformat(), rowid))
        c.execute("UPDATE sessions SET last_seen=? WHERE id IN (?,?,?)", (now.isoformat(), checkout['session_id'], search['session_id'], payments['session_id']))
    print(f'Demo desk written to {db} (repos in {root}); open /v2?project=acme-web')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'data/demo/desk.sqlite3')
