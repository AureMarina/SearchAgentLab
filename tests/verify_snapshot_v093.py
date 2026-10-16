"""Verify the delivered 0.9.3 snapshot against its original 0.9.2 archive.

Read-only checks of original records, imports, and the explicit unbound source
attachments. This does not create evaluations or recalculate model scores.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile


SOURCE = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def table_rows(connection, table):
    quoted = '"' + table.replace('"', '""') + '"'
    rows = []
    for row in connection.execute('SELECT * FROM ' + quoted):
        values = [{"blob_sha256": sha(v)} if isinstance(v, bytes) else v for v in row]
        rows.append(json.dumps(values, ensure_ascii=False, separators=(',', ':')))
    return sorted(rows)


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def verify(input_zip, package_root):
    report = {"version": "0.9.3", "verified_at": datetime.now(timezone.utc).isoformat(),
              "input_zip_sha256": sha(input_zip.read_bytes()), "database_integrity": [],
              "imports": [], "checks": [], "model_execution": False}

    def check(name, passed, detail=None):
        item = {"name": name, "passed": bool(passed)}
        if detail is not None:
            item['detail'] = detail
        report['checks'].append(item)

    with zipfile.ZipFile(input_zip) as archive, tempfile.TemporaryDirectory(prefix='searchagent-v093-input-') as temp:
        roots = {name.split('/')[0] for name in archive.namelist() if name.endswith('/data/searchagent_lab.sqlite3')}
        if len(roots) != 1:
            raise ValueError('Expected one SearchAgent Lab package root in the input ZIP')
        root = roots.pop()
        for name in sorted(archive.namelist()):
            relative = name[len(root) + 1:] if name.startswith(root + '/') else ''
            if not relative or name.endswith('/'):
                continue
            if relative.startswith('imports/'):
                path = package_root / relative
                raw = archive.read(name)
                same = path.is_file() and path.read_bytes() == raw
                report['imports'].append({"path": relative, "sha256": sha(raw), "identical_to_input": same})
                check('Original import: ' + relative, same)
            if relative.startswith('data/') and relative.endswith('.sqlite3'):
                raw = archive.read(name)
                before = Path(temp) / Path(relative).name
                before.write_bytes(raw)
                after = package_root / relative
                result = {"path": relative, "sha256": sha(after.read_bytes()),
                          "identical_to_input": raw == after.read_bytes(), "tables": []}
                a, b = readonly(before), readonly(after)
                try:
                    result['integrity_check'] = b.execute('PRAGMA integrity_check').fetchone()[0]
                    check('SQLite integrity: ' + relative, result['integrity_check'] == 'ok')
                    tables_a = {r[0] for r in a.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                    tables_b = {r[0] for r in b.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                    check('Original tables retained: ' + relative, tables_a <= tables_b)
                    for table in sorted(tables_a):
                        old, new = table_rows(a, table), table_rows(b, table)
                        same = old == new
                        preserved = not (Counter(old) - Counter(new))
                        record = {"table": table, "before_rows": len(old), "after_rows": len(new),
                                  "same_records": same, "original_records_preserved": preserved,
                                  "sha256": sha('\n'.join(new).encode())}
                        result['tables'].append(record)
                        if Path(relative).name == 'method_evidence.sqlite3':
                            check('Original method records retained: ' + table, preserved)
                        else:
                            check('Original records unchanged: ' + relative + '/' + table, same)
                    result['added_tables'] = {table: len(table_rows(b, table)) for table in sorted(tables_b - tables_a)}
                    check('New non-method tables contain no fabricated records: ' + relative,
                          relative.endswith('/method_evidence.sqlite3') or all(v == 0 for v in result['added_tables'].values()))
                finally:
                    a.close(); b.close()
                report['database_integrity'].append(result)
        skill_source = 'source/examples/skillrl/SkillRL_skills_20261005.zip'
        check('Original SkillRL source archive unchanged', archive.read(root + '/' + skill_source) == (package_root / skill_source).read_bytes())

    receipt = json.loads((package_root / 'source/docs/v093/SNAPSHOT_ATTACHMENTS.json').read_text())
    connection = readonly(package_root / 'data/method_evidence.sqlite3')
    try:
        objects = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM objects')]
        kinds = Counter(o['kind'] for o in objects)
        report['attachments'] = {"object_count": len(objects), "kinds": dict(kinds),
                                 "scope": "experiment-level unbound", "receipt": 'source/docs/v093/SNAPSHOT_ATTACHMENTS.json'}
        check('Only the declared source metadata was added', kinds == {'method_profile': 3, 'skill_archive': 1, 'skill_bank': 23})
        check('No source archive bound to a run', all(not o['scope'].get('run_id') for o in objects))
        expected = {p['object_id'] for p in receipt['profiles']} | set(receipt['archive']['object_ids'])
        check('Attachment identities match the written receipt', {o['id'] for o in objects} == expected)
        check('No synthetic demo records in source attachments', all(o.get('demo') is not True for o in objects))
    finally:
        connection.close()
    report['passed'] = all(c['passed'] for c in report['checks'])
    report['original_import_files_preserved'] = sum(i['identical_to_input'] for i in report['imports'])
    report['unchanged_database_files'] = sum(d['identical_to_input'] for d in report['database_integrity'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-zip', type=Path, required=True)
    parser.add_argument('--package-root', type=Path, default=SOURCE.parent)
    parser.add_argument('--output', type=Path, default=SOURCE / 'docs/v093/data-integrity.json')
    args = parser.parse_args()
    report = verify(args.input_zip, args.package_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('passed', 'original_import_files_preserved', 'unchanged_database_files', 'attachments')}, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
