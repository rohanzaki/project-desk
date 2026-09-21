"""One-off migration of this installation's pre-Desk Markdown rosters.

Kept for provenance. It hardcodes the project slug it was written for and is
not part of the general tool — new installations do not need it."""
"""One-time import of the shared Markdown roster; preserves reported status and evidence."""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from store import Store, scopes, now


def import_roster(store,source,archive):
    source=Path(source); original=source.read_text()
    Path(archive).write_text(original)
    imported=[]
    with store.connection(True) as c:
        for line in original.splitlines():
            if not line.startswith('| ') or line.startswith('| Task '): continue
            cells=[v.strip() for v in line.strip().strip('|').split('|')]
            if len(cells)!=5 or 'project-desk-build' in cells[0]: continue
            label,state,location,owned,next_step=cells
            status=next((v for v in ('DONE','RUNNING','BLOCKED','PAUSED') if v in state),None)
            if not status:continue
            suffix=hashlib.sha256(label.encode()).hexdigest()[:12]
            sid,tid='legacy-s-'+suffix,'legacy-t-'+suffix
            kind='claude' if 'Claude' in label else 'codex'
            codes=re.findall(r'`([^`]+)`',location)
            branch=next((v for v in codes if not v.startswith('/')),'legacy-report')
            worktree=next((v for v in codes if v.startswith('/')),'/path/to/Windows/Application_Node_Js/media_inteligence')
            resources=re.findall(r'`([^`]+)`',owned)
            resources=scopes(resources) if resources else ['service:legacy-'+label.split('/')[0].strip()]
            match=re.search(r'\d{4}-\d\d-\d\d \d\d:\d\d',state)
            stamp=datetime.strptime(match.group(),'%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo('Asia/Karachi')).isoformat() if match else now()
            c.execute('INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,?,?,?,?,1)',
                (sid,'imported-no-credential-'+suffix,label.split('/',1)[-1].strip(),kind,'media-intelligence',branch,worktree,stamp))
            c.execute('''INSERT OR IGNORE INTO tasks(id,project,title,owner,status,resources,next_step,summary,
                validation,deployment,updated,imported) VALUES(?,'media-intelligence',?,?,?,?,?,?,?,?,?,1)''',
                (tid,label.split('/')[0].strip(),sid,status,json.dumps(resources),next_step,
                 next_step if status=='DONE' else '',
                 'Imported report, not independently revalidated: '+next_step if status=='DONE' else '',
                 'deployed' if status=='DONE' and 'deployed as prod' in next_step else 'not_deployed',stamp))
            # Historical parent/child claims may overlap; preserve all reservations rather than silently drop one.
            if status!='DONE':
                c.executemany('INSERT OR IGNORE INTO claims VALUES(?,?,?)',[('media-intelligence',r,tid) for r in resources])
            self_data={'task_id':tid,'source':str(archive),'reported_status':status}
            store.event(c,'media-intelligence','migration','task.imported',self_data)
            imported.append(tid)
    if source.read_text()!=original:
        raise RuntimeError('Roster changed during import. Review latest rows before cutover; original remains untouched.')
    return imported

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--db',required=True);p.add_argument('--archive',required=True)
    a=p.parse_args();print(json.dumps(import_roster(Store(a.db),a.source,a.archive)))
