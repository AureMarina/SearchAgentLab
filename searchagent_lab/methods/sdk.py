"""Small logging interface for an existing harness's evidence exporter."""
from pathlib import Path
from .repository import MethodRepository
from .schema import SCHEMA
from ..store import Store
from ..inspection.repository import InspectionRepository

class MethodRecorder:
    def __init__(self,data_dir):
        self.store=Store(data_dir)
        self.inspections=InspectionRepository(data_dir)
        self.repo=MethodRepository(data_dir,self.store,self.inspections)
    def close(self):self.repo.close();self.inspections.close();self.store.close()
    def __enter__(self):return self
    def __exit__(self,*a):self.close()
    def record(self,object,scope=None):
        """Register one immutable record. Parents must already exist."""
        return self.repo.import_bundle({'method_schema':SCHEMA,'objects':[object]},scope=scope)
    def record_batch(self,objects,scope=None,demo=False):
        """Atomically register related records; order does not imply lineage."""
        return self.repo.import_bundle({'method_schema':SCHEMA,'objects':objects,'demo':demo},scope=scope)
    def import_file(self,path,scope=None,snapshot_id=None):
        p=Path(path);return self.repo.import_bytes(p.read_bytes(),p.name,scope,snapshot_id)
    def register_skill_bank(self,path,scope=None,snapshot_id=None):
        return self.import_file(path,scope,snapshot_id)

    def method_profile(self, record_id, method_name, families, configuration, scope):
        """Families are explicit; no project-name inference or automatic model calls."""
        return self.record({'id':record_id,'kind':'method_profile','title':method_name,
                            'data':{'method_name':method_name,'families':families,'configuration':configuration}},scope)

    def skillrl_archive(self, path, scope=None):
        """Import saved SkillRL snapshots; never infer unlogged per-question usage."""
        return self.import_file(path,scope)

    def entropy_definition(self, record_id, definition, scope):
        """Freeze a definition. A changed estimator/mask/distribution needs a new ID."""
        return self.record({'id':record_id,'kind':'entropy_definition',
                            'title':definition.get('name',record_id),'data':definition},scope)

    def entropy_observation(self, record_id, observation, scope):
        """Append a decision/probe observation referencing its immutable definition."""
        return self.record({'id':record_id,'kind':'entropy_observation','data':observation},scope)
