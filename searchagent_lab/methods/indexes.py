"""Rebuildable read indexes. Immutable `objects` and `imports` remain the source.

All tables live in the existing method sidecar, including in TeamSync's single
attached transaction. Never build these indexes by reading arbitrary source paths.
"""
from __future__ import annotations
import json
from .schema import direct_refs, evidence_refs
from .skills import adapt_bank
from ..inspection.repository import encode

TABLES = ('objects', 'imports', 'reviews', 'method_summaries', 'method_links',
          'method_import_objects', 'skill_members')
DDL = '''
CREATE TABLE IF NOT EXISTS method_summaries(id TEXT PRIMARY KEY, summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS method_links(
 source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,
 PRIMARY KEY(source_id,target_id,relation));
CREATE INDEX IF NOT EXISTS method_link_target ON method_links(target_id,source_id);
CREATE TABLE IF NOT EXISTS method_import_objects(
 import_id TEXT NOT NULL, object_id TEXT NOT NULL, PRIMARY KEY(import_id,object_id));
CREATE INDEX IF NOT EXISTS method_import_target ON method_import_objects(object_id,import_id);
CREATE TABLE IF NOT EXISTS skill_members(
 bank_id TEXT NOT NULL, member_key TEXT NOT NULL, pointer TEXT NOT NULL,
 source_id TEXT, group_name TEXT NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL,
 content_hash TEXT NOT NULL, content TEXT NOT NULL, search_text TEXT NOT NULL,
 ambiguous INTEGER NOT NULL, PRIMARY KEY(bank_id,pointer));
CREATE INDEX IF NOT EXISTS skill_member_lookup ON skill_members(bank_id,member_key);
CREATE INDEX IF NOT EXISTS skill_member_group ON skill_members(bank_id,group_name);
'''
SUMMARY_KEYS = ('id','kind','title','version','scope','hash','content_hash','usage','demo','imported_at')

def index_object(repo, obj, row):
    summary = {k: obj.get(k) for k in SUMMARY_KEYS}
    summary.update(hash=row['hash'], content_hash=row['content_hash'], imported_at=row['created_at'])
    from .projection import KINDS,VERSION,object_projection
    if obj['kind'] in KINDS:
        summary['assessment_version']=VERSION
        summary['assessment']=object_projection(repo,obj)
    if 'method_meta' in obj: summary['method_meta'] = obj['method_meta']
    repo.conn.execute('INSERT OR REPLACE INTO method_summaries VALUES (?,?)', (obj['id'], encode(summary)))
    links = {(target, relation) for target, _, relation in direct_refs(obj)}
    links.update((ref['object_id'], 'evidence_ref') for ref in evidence_refs(obj) if ref.get('object_id'))
    if obj['kind'] == 'evaluation_binding':
        links.add(('evaluation:' + obj['data']['evaluation_id'], 'evaluation_binding'))
    repo.conn.executemany('INSERT OR IGNORE INTO method_links VALUES (?,?,?)',
                         [(obj['id'], target, relation) for target, relation in links])
    if obj['kind'] == 'skill_bank':
        members = adapt_bank(obj['data'])['members']
        from .skill_tracking import bank_asset_members
        members += bank_asset_members(repo,obj['data'])
        repo.conn.executemany('INSERT OR REPLACE INTO skill_members VALUES (?,?,?,?,?,?,?,?,?,?,?)', [
            (obj['id'], m['key'], m['pointer'], m['source_id'], m['group'], m['category'],
             str(m['title']), m['content_hash'], encode(m['content']),
             (m['key'] + ' ' + encode(m['content'])).casefold(),
             int(bool(m.get('ambiguous_key') or m['source_id_missing']))) for m in members])


def migrate(repo):
    repo.conn.executescript(DDL)
    # The normal server startup reads only missing IDs, not every payload.
    missing = repo.conn.execute('SELECT o.* FROM objects o LEFT JOIN method_summaries s ON s.id=o.id WHERE s.id IS NULL').fetchall()
    imports = repo.conn.execute('SELECT i.id,i.object_ids FROM imports i WHERE NOT EXISTS '
                               '(SELECT 1 FROM method_import_objects x WHERE x.import_id=i.id)').fetchall()
    if not missing and not imports: return
    repo.conn.execute('BEGIN IMMEDIATE')
    try:
        for row in missing: index_object(repo, json.loads(row['payload']), row)
        for row in imports:
            repo.conn.executemany('INSERT OR IGNORE INTO method_import_objects VALUES (?,?)',
                                 [(row['id'], oid) for oid in json.loads(row['object_ids'])])
        repo.conn.commit()
    except BaseException:
        repo.conn.rollback(); raise


def ensure_assessment_index(repo,scope,kinds):
    """Upgrade only requested old small event summaries, lazily and in batches.

    Existing bank bodies and trace bodies are not scanned when an assessment is
    opened. New imports are projected once in their original atomic transaction.
    """
    from .projection import VERSION,object_projection
    where,args=repo._where(scope,prefix='o.')
    extra=(' AND ' if where else ' WHERE ')+"o.kind IN ("+','.join('?' for _ in kinds)+") AND coalesce(json_extract(s.summary,'$.assessment_version'),0)<?"
    query=("SELECT o.id,o.kind,CASE WHEN o.kind='skill_bank' THEN NULL ELSE o.payload END AS payload,json_extract(o.payload,'$.data.asset_ids') AS bank_asset_ids,s.summary FROM objects o JOIN method_summaries s ON s.id=o.id"+where+extra+' ORDER BY o.rowid LIMIT 100')
    with repo.lock:
        while True:
            rows=repo.conn.execute(query,args+list(kinds)+[VERSION]).fetchall()
            if not rows:break
            for row in rows:
                summary=json.loads(row['summary'])
                # Skill text lives in skill_members and is not needed here.
                projected=({'asset_ids':json.loads(row['bank_asset_ids'])} if row['bank_asset_ids'] is not None else {}) if row['kind']=='skill_bank' else object_projection(repo,json.loads(row['payload']))
                summary.update(assessment_version=VERSION,assessment=projected)
                repo.conn.execute('UPDATE method_summaries SET summary=? WHERE id=?',(encode(summary),row['id']))
