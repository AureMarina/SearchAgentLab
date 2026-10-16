"""Verify delivered data and imports against the accepted 0.9.3 ZIP, read-only."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

ROOT=Path(__file__).resolve().parents[1]
INPUT_SHA256='02a4a90c078a3c75f4da635c12bce9319afbe163a80645b2fc1ba2ad23b8319f'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-zip',type=Path,required=True)
    parser.add_argument('--data-dir',type=Path,default=ROOT.parent/'data')
    parser.add_argument('--imports-dir',type=Path,default=ROOT.parent/'imports')
    args=parser.parse_args()
    original=args.input_zip.resolve(); input_hash=hashlib.sha256(original.read_bytes()).hexdigest()
    if input_hash!=INPUT_SHA256:raise ValueError('Expected the accepted 0.9.3 source ZIP')
    report={'version':'0.9.4','verified_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'input_zip':original.name,'input_sha256':input_hash,'read_only':True,'databases':{},'imports':[],
            'model_or_training_executed':False,'added_research_records':0}
    with zipfile.ZipFile(original) as archive:
        if archive.testzip() is not None:raise ValueError('Input ZIP CRC failure')
        for label, directory in [('data',args.data_dir),('imports',args.imports_dir)]:
            prefix='SearchAgentLab_0.9.3_20261010/'+label+'/'
            wanted={name[len(prefix):]:name for name in archive.namelist() if name.startswith(prefix) and not name.endswith('/')}
            current={p.relative_to(directory).as_posix():p for p in directory.rglob('*') if p.is_file()}
            if set(wanted)!=set(current):raise ValueError(label+' file inventory differs')
            for relative,p in sorted(current.items()):
                raw=p.read_bytes();old=archive.read(wanted[relative])
                if raw!=old:raise ValueError('Saved input content changed: '+label+'/'+relative)
                receipt={'path':relative,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'byte_identical_to_input':True}
                if label=='imports':report['imports'].append(receipt);continue
                with sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as conn:
                    integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
                    if integrity!='ok':raise ValueError(relative+' integrity: '+integrity)
                    tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                    counts={table:conn.execute('SELECT count(*) FROM "'+table.replace('"','""')+'"').fetchone()[0] for table in tables}
                    receipt.update(sqlite_integrity=integrity,tables=counts)
                    if relative=='method_evidence.sqlite3':
                        receipt['kind_counts']={row[0]:row[1] for row in conn.execute('SELECT kind,count(*) FROM objects GROUP BY kind ORDER BY kind')}
                        receipt['new_skill_instance_count']=conn.execute("SELECT count(*) FROM objects WHERE kind IN ('skill_instance','skill_asset','skill_evolution','skill_comparison')").fetchone()[0]
                report['databases'][relative]=receipt
    report['passed']=True
    output=ROOT/'docs/v094/data-integrity.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':True,'databases':len(report['databases']),'source_files':len(report['imports']),
                      'all_bytes_unchanged':True,'report':str(output)},ensure_ascii=False))


if __name__=='__main__':main()
