"""Optional run registration; no torch/veRL import and no network calls.

Existing Recorder / StudioRecorder calls remain valid. A separate `team-watch`
process transports what those calls have durably recorded.
"""
from __future__ import annotations
import importlib.metadata
import platform
import subprocess
import uuid
from pathlib import Path
from ..core import ValidationError
from ..store import Store
from .package import source
from .util import sha


def runtime_snapshot(code_dir=None):
    """Allowlisted runtime facts only; never dumps environment variables or git remotes."""
    data={'python':platform.python_version(),'python_implementation':platform.python_implementation(),
          'system':platform.system(),'release':platform.release(),'machine':platform.machine(),'packages':{}}
    for name in ('searchagent-lab','torch','transformers','vllm','ray','verl','numpy'):
        try:data['packages'][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:data['packages'][name]='not_installed_or_no_package_metadata'
    root=Path(code_dir or Path.cwd()).expanduser().resolve()
    def git(*args):
        return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,timeout=5).stdout
    try:
        commit=git('rev-parse','HEAD').decode().strip();state=git('status','--porcelain=v1','--untracked-files=normal')
        diff=git('diff','HEAD','--binary')
        data['code']={'directory':str(root),'git_commit':commit,'dirty':bool(state),
                      'tracked_diff_sha256':sha(diff),'status_sha256':sha(state),
                      'working_tree_archived':False,'note':'Dirty/untracked contents are not archived by this snapshot. Register a reviewed code archive explicitly.'}
    except (OSError,subprocess.SubprocessError,UnicodeError):data['code']={'directory':str(root),'git_commit':'unknown','working_tree_archived':False}
    return data


def register_team_run(data_dir,experiment_id,name,*,seed=None,base_model='',config=None,platform_job_id='',code_dir=None,extra=None):
    """Call before training; return stable UUID to pass into the existing logger."""
    s=source(data_dir);rid='run-'+uuid.uuid4().hex
    if config is not None and not isinstance(config,dict):raise ValidationError('Actual run config must be a JSON object')
    record=dict(extra or {})
    reserved={'id','name','project_id','experiment_id','config','provenance','runtime_snapshot','seed','base_model'}
    if reserved.intersection(record):raise ValidationError('extra cannot override run identity/config/provenance')
    with Store(data_dir) as store:
        exp=store.experiment(experiment_id)
        record.update(id=rid,name=name,project_id=exp['project_id'],experiment_id=exp['id'],seed=seed,base_model=base_model,
                      config=config or {},runtime_snapshot=runtime_snapshot(code_dir),
                      provenance={'operator':s['operator'],'platform':s['platform'],'platform_job_id':platform_job_id,
                                  'workspace_id':s['workspace_id'],'registered_before_training':True})
        store.register_run(record)
    return rid
