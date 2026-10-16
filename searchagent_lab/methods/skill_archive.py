"""Import the manifest-backed SkillRL snapshot ZIP, preserving its original bytes.

Filename step labels are provenance, not proof of optimizer updates. Observed
snapshot differences do not establish creation timestamps or skill effectiveness.
"""
from __future__ import annotations
import hashlib
import io
import re
import stat
import zipfile
from pathlib import PurePosixPath
from ..core import ValidationError
from ..inspection.repository import digest, strict_json, MAX_FILE_BYTES
from .schema import SCHEMA
from .skills import adapt_bank, compare_banks


def _layout(name):
    if re.fullmatch(r'initial/[^/]+\.json',name):return 'initial', None
    m=re.fullmatch(r'training/updated_skills_step(\d+)\.json',name)
    if m:return 'training', int(m[1])
    m=re.fullmatch(r'evaluation_step(\d+)/frozen_skills_step(\d+)\.json',name)
    if m and m[1]==m[2]:return 'evaluation', int(m[1])
    raise ValidationError('Unsupported snapshot layout; supply explicit method objects instead: '+name[:150])


def import_archive(repo, raw, name, scope=None):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_FILE_BYTES:
        raise ValidationError('Archive must be 1 byte–50 MiB')
    sc=repo._scope(scope or {}); archive_hash=hashlib.sha256(raw).hexdigest()
    aid='skills-'+digest([archive_hash,sc])[:28]
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos=z.infolist()
            if len(infos)>2100 or len({f.filename for f in infos})!=len(infos):
                raise ValidationError('Too many or duplicate ZIP entries')
            if sum(f.file_size for f in infos)>MAX_FILE_BYTES:
                raise ValidationError('Uncompressed archive exceeds 50 MiB')
            for f in infos:
                path=PurePosixPath(f.filename)
                if '\\' in f.filename or path.is_absolute() or '..' in path.parts or ':' in f.filename or '\x00' in f.filename:
                    raise ValidationError('Unsafe ZIP entry name')
                if stat.S_ISLNK(f.external_attr>>16) or f.flag_bits&1:
                    raise ValidationError('Symlink / encrypted ZIP entries are not supported')
            if 'manifest.json' not in z.namelist():raise ValidationError('SkillRL archive requires manifest.json')
            manifest=strict_json(z.read('manifest.json').decode('utf-8-sig'))
            if not isinstance(manifest,dict) or not isinstance(manifest.get('files'),list) or not manifest['files']:
                raise ValidationError('manifest.files must be a nonempty list')
            files=manifest['files']
            if len(files)>2000:raise ValidationError('Too many snapshots')
            seen=set(); rows=[]
            for entry in files:
                if not isinstance(entry,dict) or not isinstance(entry.get('file'),str):raise ValidationError('Invalid manifest entry')
                fn=entry['file']
                if fn in seen:raise ValidationError('Repeated snapshot in manifest')
                seen.add(fn)
                phase,step=_layout(fn)
                if fn not in z.namelist():raise ValidationError('Missing snapshot: '+fn)
                content=z.read(fn); file_hash=hashlib.sha256(content).hexdigest()
                if entry.get('sha256')!=file_hash:raise ValidationError('Snapshot SHA256 mismatch: '+fn)
                bank=strict_json(content.decode('utf-8-sig'))
                view=adapt_bank(bank)
                if 'general_skill_count' in entry and entry['general_skill_count']!=len(bank.get('general_skills',[])):
                    raise ValidationError('Manifest general_skill_count mismatch: '+fn)
                rows.append((phase,step,fn,entry,bank,view,file_hash))
            unlisted=[f.filename for f in infos if f.filename.endswith('.json') and f.filename!='manifest.json' and f.filename not in seen]
            if unlisted:raise ValidationError('Unlisted JSON evidence in archive: '+unlisted[0])
    except (zipfile.BadZipFile,KeyError,UnicodeDecodeError,ValueError,RuntimeError) as exc:
        if isinstance(exc,ValidationError):raise
        raise ValidationError('Invalid SkillRL ZIP: '+str(exc)[:200]) from exc
    rows.sort(key=lambda r:({'initial':0,'training':1,'evaluation':2}[r[0]],r[1] or 0,r[2]))
    training_keys=[(x[0],x[1]) for x in rows if x[0]!='evaluation']
    if len(training_keys)!=len(set(training_keys)):
        raise ValidationError('Ambiguous snapshot order: duplicate initial/training step; preserve separate runs')
    objects=[];bank_ids=[];snapshots=[];previous=None;training_hashes={}
    for i,(phase,step,fn,entry,bank,view,file_hash) in enumerate(rows):
        bid=aid+'-b'+str(i).zfill(4);bank_ids.append(bid)
        counts={g:sum(m['group']==g for m in view['members']) for g in ('general_skills','query_type_skills','task_specific_skills','common_mistakes')}
        counts.update(skill_count=view['skill_count'],mistake_count=view['mistake_count'])
        diff=compare_banks(previous,bank) if previous is not None and phase=='training' else None
        s={'phase':phase,'step':step,'step_source':'archive_filename' if step is not None else 'initial_snapshot',
           'file':fn,'source_path':entry.get('source_path'),'file_sha256':file_hash,'content_hash':digest(bank),
           'counts':counts,'changes':diff.get('counts') if diff else None,
           'comparison_available':diff.get('comparable') if diff else None,
           'warnings':view['warnings']}
        if phase=='evaluation':
            s['byte_identical_training_index']=training_hashes.get(file_hash)
        else:
            previous=bank;training_hashes[file_hash]=i
        snapshots.append(s)
        objects.append({'id':bid,'kind':'skill_bank','scope':sc,'version':fn,'title':fn,
                        'usage':'unknown','data':bank,
                        'method_meta':{'family':'skill','source_archive_sha256':archive_hash,
                                       'source_file':fn,'source_sha256':file_hash,'phase':phase,'step':step}})
    archive={'id':aid,'kind':'skill_archive','scope':sc,'title':str(name),'usage':'unknown',
        'method_meta':{'family':'skill','adapter':'skillrl-snapshot-zip/v1'},
        'data':{'bank_ids':bank_ids,'snapshots':snapshots,'archive_sha256':archive_hash,
                'source_manifest':manifest,'collection':'SkillRL snapshot archive',
                'coverage':{'snapshots':'recorded','contents':'recorded','version_differences':'derived_from_saved_snapshots',
                            'generation_sources':'not_recorded','candidate_selection':'not_recorded',
                            'prompt_injection':'not_recorded','training_consumption':'not_recorded',
                            'credit_assignment':'not_recorded','autonomous_evaluation_scores':'not_recorded'},
                'limitations':['Only saved snapshots are available; overwritten or unsaved history is unknown.',
                    'First appearance is not an exact creation time or an acceptance event.',
                    'Evaluation filename is not a formal evaluation ID; no checkpoint, score or trajectory is invented.']}}
    result=repo.import_bundle({'method_schema':SCHEMA,'scope':sc,'objects':[archive]+objects},raw,name,sc)
    result.update(kind='skill_archive',archive_id=aid,snapshot_count=len(bank_ids),snapshot_id=None)
    return result
