from pathlib import Path
import io
import json
import re
import zipfile

import pytest

import onboarding

P = {'slug': 'xyz-app', 'name': 'XYZ App', 'rules_path': '', 'repo_roots': []}
DESK = 'http://127.0.0.1:7331'
PLACEHOLDER = re.compile(r'\{(project|name|desk_url|dashboard_url|desk_cli|desk_root)\}')


def test_rules_are_filled_and_generic():
    text = onboarding.render_rules(P, DESK)
    assert '`xyz-app`' in text and 'http://127.0.0.1:7331/p/xyz-app' in text
    assert not PLACEHOLDER.search(text)
    for private in ('Media Intelligence', 'SERVER-BLUEPRINT', 'CODEX-WORKING-NOTES', 'pm2'):
        assert private not in text


def test_project_rules_file_wins(tmp_path):
    own = tmp_path / 'AGENTS.md'
    own.write_text('Our own rules\n')
    assert onboarding.render_rules({**P, 'rules_path': str(own)}, DESK) == 'Our own rules\n'
    assert '`xyz-app`' in onboarding.render_rules({**P, 'rules_path': str(tmp_path / 'missing.md')}, DESK)


def test_declaration_and_blocks():
    assert json.loads(onboarding.declaration(P, DESK + '/')) == {
        'project': 'xyz-app', 'name': 'XYZ App', 'desk': DESK}
    for block in (onboarding.agents_block(P, DESK), onboarding.claude_block(P, DESK)):
        assert block.startswith(onboarding.BEGIN) and block.rstrip().endswith(onboarding.END)
        assert block.count(onboarding.BEGIN) == 1


def test_onboard_page_has_the_command_and_the_files():
    page = onboarding.onboard_md(P, DESK)
    assert f'{onboarding.ROOT}/desk join {DESK}/p/xyz-app' in page
    assert f'{DESK}/p/xyz-app/rules' in page
    assert '"project": "xyz-app"' in page and onboarding.BEGIN in page
    assert 'claude mcp add --transport http --scope user project-desk' in page


def test_kit():
    archive = zipfile.ZipFile(io.BytesIO(onboarding.kit_zip(P, DESK)))
    assert tuple(archive.namelist()) == onboarding.KIT_FILES
    assert archive.read('.project-desk.json').decode() == onboarding.declaration(P, DESK)
    with pytest.raises(KeyError):
        onboarding.kit_file('../secret', P, DESK)


def test_committed_repo_files_carry_no_local_paths():
    """Files `desk join` writes into a repo get committed, possibly publicly:
    they must never contain this machine's install path or home folder."""
    for name in ('.project-desk.json', 'AGENTS.project-desk.md', 'CLAUDE.project-desk.md'):
        text = onboarding.kit_file(name, P, DESK)
        assert str(onboarding.ROOT) not in text, name
        assert str(Path.home()) not in text, name
        assert '/home/' not in text and '/Users/' not in text, name
    # The per-machine pages (served locally, never committed) still give the exact commands.
    assert str(onboarding.ROOT) in onboarding.onboard_md(P, DESK)
    assert str(onboarding.ROOT) in onboarding.setup_md(P, DESK)
