"""Browser coverage for the project dropdown, /p/<slug> links and New project.

Starts a test-only desk on a free port against a temporary database; never touches 7331."""
import os
import subprocess

import pytest
from playwright.sync_api import expect, sync_playwright

from store import Store
from test_discussions_e2e import PYTHON, ROOT, free_port, wait_for_service


@pytest.fixture
def desk_service(tmp_path):
    db = tmp_path / 'desk.sqlite3'
    desk = Store(db)
    mi = desk.register('MI agent', 'codex', 'media-intelligence', 'main', '/tmp/mi-e2e')
    pl = desk.register('Shop agent', 'claude', 'shop-app', 'main', '/tmp/pl-e2e')
    desk.claim(mi['session_key'], 'MI task', ['src/mi'], 'Do MI work')
    desk.claim(pl['session_key'], 'Shop task', ['src/pl'], 'Do shop work')
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'PROJECT_DESK_PORT': str(port), 'PROJECT_DESK_DB': str(db),
           'PROJECT_DESK_ROSTER': str(tmp_path / 'ROSTER.md')}
    process = subprocess.Popen([str(PYTHON), 'server.py'], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        wait_for_service(base)
        yield {'base': base, 'desk': desk}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser.new_page(viewport={'width': 1440, 'height': 1000})
        browser.close()


def open_page(page, url):
    page.goto(url)
    page.wait_for_load_state('networkidle')


def test_dropdown_lists_projects_and_new_project(desk_service, page):
    open_page(page, desk_service['base'])
    expect(page.locator('#project option')).to_contain_text(['Media Intelligence', 'Shop App', '+ New project'])
    expect(page.locator('#project')).to_have_value('media-intelligence')


def test_project_link_selects_that_board(desk_service, page):
    open_page(page, desk_service['base'] + '/p/shop-app')
    expect(page.locator('#project')).to_have_value('shop-app')
    expect(page.get_by_text('Shop task').first).to_be_visible()
    expect(page.get_by_text('MI task')).to_have_count(0)


def test_switching_updates_the_address(desk_service, page):
    open_page(page, desk_service['base'])
    page.select_option('#project', 'shop-app')
    expect(page).to_have_url(desk_service['base'] + '/p/shop-app')
    expect(page.get_by_text('Shop task').first).to_be_visible()


def test_new_project_then_connect_agents(desk_service, page):
    open_page(page, desk_service['base'])
    page.select_option('#project', '__new__')
    page.fill('#editor-fields input[name="name"]', 'XYZ App')
    page.fill('#editor-fields input[name="slug"]', 'xyz-app')
    page.fill('#editor-fields textarea[name="repo_roots"]', '/srv/repos/xyz')
    page.click('#save')
    expect(page.locator('#editor-title')).to_have_text('Connect agents · XYZ App')
    expect(page.locator('#editor-fields textarea').first).to_have_value(
        f"Set up Project Desk for this repo from {desk_service['base']}/p/xyz-app/onboard")
    expect(page).to_have_url(desk_service['base'] + '/p/xyz-app')


def test_unknown_project_link_is_reported_not_created(desk_service, page):
    open_page(page, desk_service['base'] + '/p/nope')
    expect(page.locator('#notice')).to_contain_text('Unknown project “nope”')
    assert all(p['slug'] != 'nope' for p in desk_service['desk'].list_projects())


def test_projects_overview(desk_service, page):
    open_page(page, desk_service['base'] + '/projects')
    expect(page.locator('.project-card')).to_have_count(2)
    page.locator('.project-card', has_text='Shop App').click()
    expect(page).to_have_url(desk_service['base'] + '/p/shop-app')


def test_shared_locks_are_shown(desk_service, page):
    other = desk_service['desk'].register('Deployer', 'codex', 'media-intelligence', 'main', '/tmp/mi-deploy')
    desk_service['desk'].claim(other['session_key'], 'Deploy', ['service:gpu-box'], 'Ship')
    open_page(page, desk_service['base'] + '/p/shop-app')
    expect(page.locator('#shared-locks')).to_contain_text('service:gpu-box')


def test_overview_hides_board_scoped_ui(desk_service, page):
    other = desk_service['desk'].register('Deployer', 'codex', 'media-intelligence', 'main', '/tmp/mi-deploy-overview')
    desk_service['desk'].claim(other['session_key'], 'Deploy', ['service:gpu-box'], 'Ship')
    open_page(page, desk_service['base'] + '/p/shop-app')
    expect(page.locator('#shared-locks')).to_contain_text('service:gpu-box')
    open_page(page, desk_service['base'] + '/projects')
    expect(page.locator('#shared-locks')).to_be_hidden()
    expect(page.locator('#activity-history')).to_be_hidden()
    expect(page.locator('.project-card')).to_have_count(2)


def test_new_project_requires_a_repo_folder(desk_service, page):
    open_page(page, desk_service['base'])
    page.select_option('#project', '__new__')
    page.fill('#editor-fields input[name="name"]', 'No Repo App')
    page.fill('#editor-fields input[name="slug"]', 'no-repo-app')
    page.click('#save')
    expect(page.locator('#form-error')).to_contain_text('Add at least one repo folder')
    assert all(p['slug'] != 'no-repo-app' for p in desk_service['desk'].list_projects())
