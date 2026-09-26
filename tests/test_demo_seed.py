"""The demo desk used for screenshots and try-outs still builds, and is fictional."""
import importlib.util
from pathlib import Path

from store import Store


def test_demo_seed_builds_a_desk_with_every_kind_of_attention(tmp_path):
    spec = importlib.util.spec_from_file_location('demo_seed', Path(__file__).resolve().parent.parent / 'scripts/demo_seed.py')
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    db = tmp_path / 'demo' / 'desk.sqlite3'
    demo.main(str(db))
    d = Store(db, strict=True)
    kinds = {i['kind'] for i in d.attention('all')['items']}
    assert {'approval', 'question', 'desk_request', 'stale', 'refused', 'action'} <= kinds
    assert {p['slug'] for p in d.list_projects()} >= {'acme-web', 'acme-api'}
