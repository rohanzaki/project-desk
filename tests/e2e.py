"""Run against an isolated service: PROJECT_DESK_PORT=7332 with a temporary DB."""
import asyncio
import json
from pathlib import Path
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from playwright.sync_api import sync_playwright

BASE='http://127.0.0.1:7332'
async def protocol():
    async with streamable_http_client(BASE+'/mcp') as (r1,w1,_):
        async with ClientSession(r1,w1) as codex:
            await codex.initialize()
            async with streamable_http_client(BASE+'/mcp') as (r2,w2,_):
                async with ClientSession(r2,w2) as claude:
                    await claude.initialize()
                    tools=await codex.list_tools()
                    assert len(tools.tools)==9
                    async def call(client,name,args,fail=False):
                        r=await client.call_tool(name,args)
                        assert bool(r.isError)==fail, r
                        return None if fail else (r.structuredContent or json.loads(r.content[0].text))
                    a=await call(codex,'register_session',{'name':'Codex E2E','agent':'codex','branch':'test/codex','worktree':'/tmp/codex-e2e'})
                    b=await call(claude,'register_session',{'name':'Claude E2E','agent':'claude','branch':'test/claude','worktree':'/tmp/claude-e2e'})
                    ak,bk=a['session_key'],b['session_key']
                    t=await call(codex,'claim_task',{'session_key':ak,'title':'Coordinate capture contract','resources':['src/capture'],'next_step':'Agree on API contract'})
                    await call(claude,'claim_task',{'session_key':bk,'title':'Conflicting work','resources':['src/capture/view.ts'],'next_step':'Should be refused'},True)
                    msg=await call(codex,'send_message',{'session_key':ak,'recipient':b['session_id'],'body':'Contract ready. Please review and accept the handoff.','task_id':t['id']})
                    inbox=await call(claude,'check_in',{'session_key':bk})
                    assert inbox['inbox'][0]['id']==msg['message_id']
                    await call(claude,'acknowledge_message',{'session_key':bk,'message_id':msg['message_id']})
                    offered=await call(codex,'offer_handoff',{'session_key':ak,'task_id':t['id'],'version':t['version'],'target_session':b['session_id']})
                    accepted=await call(claude,'accept_handoff',{'session_key':bk,'task_id':t['id'],'version':offered['version']})
                    assert accepted['owner']==b['session_id']
                    await call(claude,'leave_note',{'session_key':bk,'body':'Use one response contract for capture consumers.','kind':'proposal'})
                    await call(claude,'update_task',{'session_key':bk,'task_id':t['id'],'version':accepted['version'],'status':'DONE','next_step':'','summary':'Contract agreed and handoff verified','validation':'Two independent MCP client connections completed the workflow','deployment':'not_applicable'})
                    await call(codex,'claim_task',{'session_key':ak,'title':'Follow-up UI work','resources':['src/capture'],'next_step':'Build against the agreed contract'})
                    print('PASS: MCP discovery, independent clients, overlap refusal, inbox, explicit acknowledgment, handoff, completion and claim release.')

asyncio.run(protocol())
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(BASE);page.wait_for_load_state('networkidle')
    page.get_by_role('heading',name='Follow-up UI work').wait_for()
    page.get_by_role('button',name='Add task',exact=True).click()
    page.get_by_label('Task title').fill('the owner dashboard task')
    page.get_by_label('Assign to').select_option('claude')
    page.get_by_label('Files or directories').fill('src/dashboard-test')
    page.get_by_label('Expected outcome').fill('Verify dashboard actions')
    page.get_by_role('button',name='Save',exact=True).click()
    row=page.locator('article').filter(has=page.get_by_role('heading',name='the owner dashboard task'))
    row.wait_for()
    row.get_by_role('button',name='Pause',exact=True).click();page.get_by_role('button',name='Save',exact=True).click()
    row.get_by_text('PAUSED',exact=True).wait_for()
    row.get_by_role('button',name='Resume',exact=True).click();page.get_by_role('button',name='Save',exact=True).click()
    row.get_by_text('QUEUED',exact=True).wait_for()
    row.get_by_role('button',name='Set priority').click();page.get_by_role('combobox',name='Priority',exact=True).select_option('urgent');page.get_by_role('button',name='Save',exact=True).click()
    row.get_by_text('urgent',exact=True).wait_for()
    page.get_by_role('button',name='Write message').click();page.get_by_label('Message',exact=True).fill('Please use the shared contract.');page.get_by_role('button',name='Save',exact=True).click()
    page.get_by_text('Please use the shared contract.',exact=True).wait_for()
    page.get_by_role('button',name='Acknowledge',exact=True).click()
    page.get_by_text('Acknowledged by the owner',exact=True).wait_for()
    page.get_by_role('button',name='Add decision').click();page.get_by_label('Decision and reason').fill('Ship UI after contract review.');page.get_by_role('button',name='Save',exact=True).click()
    page.get_by_text('Ship UI after contract review.',exact=True).wait_for()
    row.get_by_role('button',name='Reassign',exact=True).click();page.get_by_label('Receiving session').select_option(label='Claude E2E');page.get_by_label('Reason / agreed handoff').fill('Explicit dashboard handoff');page.get_by_role('button',name='Save',exact=True).click()
    row.get_by_text('Claude E2E',exact=False).first.wait_for()
    page.screenshot(path='artifacts/dashboard-desktop.png',full_page=True)
    row.get_by_role('button',name='Close with note').click();page.get_by_label('Outcome or cancellation reason').fill('Dashboard flow validated');page.get_by_role('button',name='Save',exact=True).click()
    page.get_by_role('combobox',name='Status',exact=True).select_option('DONE')
    page.get_by_role('heading',name='the owner dashboard task').wait_for()
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path='artifacts/dashboard-mobile.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors,errors
    browser.close()
print('PASS: browser task creation, pause/resume, priority, message, acknowledgment, decision, reassignment, closure, filters and mobile layout.')
