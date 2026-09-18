"""Browser coverage for task discussions, replies, receipts, and reassignment.

The fixture starts a test-only Project Desk process against a temporary SQLite
database and never touches the live service on port 7331.
"""

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from store import Store


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_service(base):
    import urllib.request

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("isolated Project Desk service did not start")


@pytest.fixture
def isolated_service(tmp_path):
    db = tmp_path / "desk.sqlite3"
    roster = tmp_path / "ROSTER.md"
    desk = Store(db)
    codex = desk.register("Codex Browser", "codex", "media-intelligence", "test/codex", "/tmp/codex-browser")
    claude = desk.register("Claude Browser", "claude", "media-intelligence", "test/claude", "/tmp/claude-browser")
    imported = desk.register("Old Imported", "claude", "media-intelligence", "old/import", "/tmp/old-import")
    with desk.connection(True) as connection:
        connection.execute("UPDATE sessions SET imported=1 WHERE id=?", (imported["session_id"],))
    task = desk.claim(codex["session_key"], "Capture review", ["src/capture"], "Review the shared capture contract")
    seeded = desk.message(
        codex["session_key"],
        "all",
        "Please review this task context.",
        task["id"],
    )

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({
        "PROJECT_DESK_PORT": str(port),
        "PROJECT_DESK_DB": str(db),
        "PROJECT_DESK_ROSTER": str(roster),
    })
    process = subprocess.Popen(
        [str(PYTHON), "server.py"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for_service(base)
        yield {
            "base": base,
            "db": db,
            "desk": desk,
            "codex": codex,
            "claude": claude,
            "imported": imported,
            "task": task,
            "seeded": seeded,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def browser(service):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(service["base"])
        page.wait_for_load_state("networkidle")
        yield page
        browser.close()


def test_task_discussion_broadcasts_and_separate_receipts(isolated_service):
    service = isolated_service
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(service["base"])
        page.wait_for_load_state("networkidle")

        task_card = page.locator("article.task").filter(has_text="Capture review")
        expect(task_card.get_by_text("Discussion", exact=False)).to_be_visible()
        expect(task_card.get_by_text("Please review this task context.", exact=True)).to_be_visible()

        task_card.get_by_role("button", name="Comment", exact=True).click()
        expect(page.get_by_role("heading", name="Comment on task")).to_be_visible()
        expect(page.locator("#editor").get_by_text("Task: Capture review", exact=False)).to_be_visible()
        expect(page.get_by_role("combobox", name="Recipient", exact=True)).to_have_value("all")
        page.get_by_label("Message", exact=True).fill("the owner's task comment")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(task_card.get_by_text("the owner's task comment", exact=True)).to_be_visible()

        messages = service["desk"].snapshot("media-intelligence")["messages"]
        comment = next(m for m in messages if m["body"] == "the owner's task comment")
        assert comment["task_id"] == service["task"]["id"]
        assert comment["recipient"] == "all"

        codex_inbox = service["desk"].check_in(service["codex"]["session_key"])["inbox"]
        claude_inbox = service["desk"].check_in(service["claude"]["session_key"])["inbox"]
        assert comment["id"] in {m["id"] for m in codex_inbox}
        assert comment["id"] in {m["id"] for m in claude_inbox}
        service["desk"].acknowledge(service["codex"]["session_key"], comment["id"])
        assert comment["id"] in {
            m["id"] for m in service["desk"].check_in(service["claude"]["session_key"])["inbox"]
        }
        service["desk"].acknowledge(service["claude"]["session_key"], comment["id"])
        comment = next(m for m in service["desk"].snapshot("media-intelligence")["messages"] if m["id"] == comment["id"])
        assert {a["session_id"] for a in comment["acknowledgments"]} == {
            service["codex"]["session_id"], service["claude"]["session_id"]
        }

        # Reply from the task discussion keeps the task and targets the author.
        task_card.get_by_role("button", name="Reply", exact=True).last.click()
        expect(page.get_by_role("heading", name="Reply in team inbox")).to_be_visible()
        expect(page.get_by_role("combobox", name="Recipient", exact=True)).to_have_value("rohan")
        page.get_by_label("Message", exact=True).fill("Reply to the task comment")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(task_card.get_by_text("Reply to the task comment", exact=True)).to_be_visible()
        reply = next(m for m in service["desk"].snapshot("media-intelligence")["messages"] if m["body"] == "Reply to the task comment")
        assert reply["task_id"] == service["task"]["id"]
        assert reply["recipient"] == "rohan"
        browser.close()


def test_team_inbox_reply_and_reassignment_are_explicit(isolated_service):
    service = isolated_service
    direct = service["desk"].message(
        service["claude"]["session_key"],
        "rohan",
        "Please confirm this task owner.",
        service["task"]["id"],
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(service["base"])
        page.wait_for_load_state("networkidle")

        inbox_message = page.locator(f'.inbox [data-message="{direct["message_id"]}"]')
        expect(inbox_message.get_by_text("Sent", exact=True).first).to_be_visible()
        inbox_message.get_by_role("button", name="Reply", exact=True).click()
        expect(page.get_by_role("combobox", name="Recipient", exact=True)).to_have_value(service["claude"]["session_id"])
        page.get_by_label("Message", exact=True).fill("Reply to Claude directly")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator(".inbox").get_by_text("Reply to Claude directly", exact=True)).to_be_visible()
        reply = next(m for m in service["desk"].snapshot("media-intelligence")["messages"] if m["body"] == "Reply to Claude directly")
        assert reply["task_id"] == service["task"]["id"]
        assert reply["recipient"] == service["claude"]["session_id"]

        page.get_by_role("tab", name="Notes", exact=True).click()
        page.get_by_role("button", name="Publish update", exact=True).click()
        expect(page.get_by_role("heading", name="Publish a progress update")).to_be_visible()
        page.get_by_label("Update title", exact=True).fill("Capture review update")
        page.get_by_role("combobox", name="Task (optional)", exact=True).select_option(service["task"]["id"])
        page.get_by_label("Update body", exact=True).fill("The browser review is ready for both agents.")
        page.get_by_label("Commit reference", exact=True).fill("abc1234")
        page.get_by_label("Validation evidence", exact=True).fill("Two independent inbox checks passed.")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator(".notebook").get_by_text("Capture review update", exact=False)).to_be_visible()
        published = next(
            m for m in service["desk"].snapshot("media-intelligence")["messages"]
            if m["body"].startswith("Capture review update\n")
        )
        assert published["task_id"] == service["task"]["id"]
        assert published["recipient"] == "all"
        changelog = next(
            n for n in service["desk"].snapshot("media-intelligence")["notes"]
            if n["kind"] == "changelog" and n["body"].startswith("Capture review update\n")
        )
        assert "Two independent inbox checks passed." in changelog["body"]

        task_card = page.locator("article.task").filter(has_text="Capture review")
        task_card.get_by_role("button", name="Reassign", exact=True).click()
        receiving = page.get_by_role("combobox", name="Receiving session", exact=True)
        expect(receiving.locator("option").first).to_have_text("Select a receiving session…")
        assert receiving.locator("option").first.get_attribute("disabled") is not None
        expect(receiving).to_have_attribute("required", "")
        expect(receiving.locator("option", has_text="Old Imported")).to_have_count(0)
        expect(receiving.locator("option", has_text="Claude Browser")).to_have_count(1)

        # The required empty placeholder cannot silently choose a session.
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_role("heading", name="Reassign ownership")).to_be_visible()

        option = receiving.locator("option").filter(has_text="Claude Browser").get_attribute("value")
        receiving.select_option(option)
        page.get_by_label("Reason / agreed handoff", exact=True).fill("Explicit browser handoff")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(task_card.locator(".task-top").get_by_text("Claude Browser", exact=False)).to_be_visible()
        updated = next(t for t in service["desk"].snapshot("media-intelligence")["tasks"] if t["id"] == service["task"]["id"])
        assert updated["owner"] == service["claude"]["session_id"]
        assert updated["version"] == service["task"]["version"] + 1
        browser.close()


def test_completed_task_can_be_reopened_and_reassigned(isolated_service):
    service = isolated_service
    completed = service["desk"].human("media-intelligence", "close", {
        "task_id": service["task"]["id"],
        "version": service["task"]["version"],
        "summary": "Initial capture review is complete",
    })

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(service["base"])
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Completed", exact=False).click()

        task_card = page.locator("article.task").filter(has_text="Capture review")
        details = task_card.locator("details.task-body")
        if details.get_attribute("open") is None:
            details.locator("summary").click()
        expect(details).to_have_attribute("open", "")
        reopen_button = task_card.locator('[data-action="reopen"]')
        expect(reopen_button).to_be_visible()
        expect(reopen_button).to_have_text("Reopen & reassign")
        reopen_button.click()
        expect(page.get_by_role("heading", name="Reopen & reassign completed task")).to_be_visible()
        receiving = page.get_by_role("combobox", name="Receiving session", exact=True)
        option = receiving.locator("option").filter(has_text="Claude Browser").get_attribute("value")
        receiving.select_option(option)
        page.get_by_label("What should happen next", exact=True).fill("Review the new follow-up evidence")
        page.get_by_role("button", name="Save", exact=True).click()

        page.get_by_role("button", name="Active", exact=False).click()
        task_card = page.locator("article.task").filter(has_text="Capture review")
        expect(task_card.locator(".task-top").get_by_text("Claude Browser", exact=False)).to_be_visible()
        expect(task_card.locator(".badge")).to_have_text("RUNNING")
        reopened = next(t for t in service["desk"].snapshot("media-intelligence")["tasks"] if t["id"] == completed["id"])
        assert reopened["owner"] == service["claude"]["session_id"]
        assert reopened["next_step"] == "Review the new follow-up evidence"
        assert reopened["summary"] == "Initial capture review is complete"
        assert reopened["version"] == completed["version"] + 1
        browser.close()


def test_notification_bell_tracks_external_updates_without_receipts(isolated_service):
    service = isolated_service
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(service["base"])
        page.wait_for_load_state("networkidle")
        count = page.locator("#notification-count")
        expect(count).to_be_hidden()

        external_task = service["desk"].human("media-intelligence", "create", {
            "title": "External notification task",
            "resources": ["src/notification-test"],
            "next_step": "Review the external update",
        })
        external_message = service["desk"].message(
            service["claude"]["session_key"],
            "all",
            "External inbox update",
            service["task"]["id"],
        )
        expect(count).to_have_text("2", timeout=8000)
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(count).to_have_text("2", timeout=8000)
        page.get_by_role("button", name="Notifications", exact=True).click()
        panel = page.locator("#notification-panel")
        expect(panel.get_by_text("External notification task", exact=False)).to_be_visible()
        expect(panel.get_by_text("External inbox update", exact=False)).to_be_visible()
        expect(page.locator("#mark-seen")).to_have_css("color", "rgb(35, 70, 117)")
        expect(panel.locator(".notification-item p").first).to_have_css("color", "rgb(23, 43, 70)")
        expect(page.locator(".notification-bell")).to_be_visible()
        page.screenshot(path="/tmp/project-desk-dashboard-desktop.png", full_page=True)
        page.get_by_role("button", name="Mark seen", exact=True).click()
        expect(count).to_be_hidden()

        stored = next(
            m for m in service["desk"].snapshot("media-intelligence")["messages"]
            if m["id"] == external_message["message_id"]
        )
        assert stored["acknowledgments"] == []
        assert external_task["status"] == "QUEUED"

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path="/tmp/project-desk-dashboard-mobile.png", full_page=True)
        browser.close()


def test_task_details_stay_open_across_live_refreshes(isolated_service):
    service = isolated_service
    for number in range(4):
        service["desk"].human("media-intelligence", "create", {
            "title": f"Refresh fixture {number}",
            "resources": [f"src/refresh-{number}"],
            "next_step": "Keep the task list large enough for compact rows",
        })

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(service["base"])
        page.wait_for_load_state("networkidle")

        task_card = page.locator("article.task").filter(has_text="Capture review")
        details = task_card.locator("details.task-body")
        assert details.evaluate("node => node.open") is False

        details.locator("summary").click()
        assert details.evaluate("node => node.open") is True
        page.wait_for_timeout(6500)
        assert details.evaluate("node => node.open") is True

        details.locator("summary").click()
        assert details.evaluate("node => node.open") is False
        page.wait_for_timeout(3500)
        assert details.evaluate("node => node.open") is False
        browser.close()
