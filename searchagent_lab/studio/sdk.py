"""Optional logging facade. Does not import veRL, Torch or run an agent."""
from ..store import Store
from ..inspection.repository import InspectionRepository
from ..methods.repository import MethodRepository
from .repository import StudioRepository

class StudioRecorder:
    def __init__(self,data_dir):
        self.store=Store(data_dir)
        self.inspections=InspectionRepository(data_dir)
        self.methods=MethodRepository(data_dir,self.store,self.inspections)
        self.repo=StudioRepository(self.store,self.methods,self.inspections)
    def close(self):
        self.repo.close();self.methods.close();self.inspections.close();self.store.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def register_experiment_profile(self,**record):return self.repo.register_profile(record)
    def log_training_batch(self,record):return self.repo.record_batch(record)
    def log_sft_batch(self, *, run_id, batch_id, step, samples, dataset_version=None, source=None):
        """Record actual SFT examples using existing immutable training batches.

        Input/target/token arrays are optional, literal exports. This method does
        not tokenize, infer a loss mask or read a frozen development panel.
        """
        from ..core import clone, ValidationError
        if not isinstance(samples,list):raise ValidationError('samples must be an array')
        rows=clone(samples)
        for sample in rows:
            if not isinstance(sample,dict):raise ValidationError('SFT sample must be an object')
            if sample.get('record_purpose') not in (None,'sft_demonstration'):
                raise ValidationError('SFT sample has conflicting record_purpose')
            # Check every purpose alias before adding the explicit SDK declaration.
            from .evaluation import _declared_record_purposes
            if _declared_record_purposes(sample)-{'sft_demonstration'}:
                raise ValidationError('Do not relabel evaluation or rollout records as SFT demonstrations')
            sample['record_purpose']='sft_demonstration'
        batch={'id':batch_id,'run_id':run_id,'step':step,'training_stage':'sft',
               'groups':[{'id':'demonstrations','samples':rows}]}
        if dataset_version is not None:batch['dataset_version']=dataset_version
        if source is not None:batch['source']=clone(source)
        return self.repo.record_batch(batch)

    def log_method_evidence(self,objects,scope=None,demo=False):
        """Atomically import explicit method events beside existing training logs.

        This is the same repository/import contract as MethodRecorder. It does
        not reconstruct missing events from scalar training metrics.
        """
        from ..methods.schema import SCHEMA
        return self.methods.import_bundle({'method_schema':SCHEMA,'objects':objects,'demo':demo},scope=scope)
    def import_method_evidence(self,path,scope=None,snapshot_id=None):
        """Import native method JSON/JSONL or a supported saved bank archive."""
        from pathlib import Path
        p=Path(path)
        return self.methods.import_bytes(p.read_bytes(),p.name,scope,snapshot_id)
    def import_slim_lifecycle(self,path,*,scope,subject_map,checkpoint_id=None,record_prefix=None,demo=False):
        """Import a saved SLIM audit with explicitly mapped frozen skill IDs.

        Original file bytes and reported utility are retained. The caller must
        supply the contemporaneous checkpoint when known; filenames do not
        establish model identity, skill versions, or controlled task gain.
        """
        from pathlib import Path
        from ..core import ValidationError
        from ..inspection.repository import MAX_FILE_BYTES
        from ..methods.skill_adapters import adapt_slim_lifecycle
        p=Path(path)
        if not 0<p.stat().st_size<=MAX_FILE_BYTES:
            raise ValidationError('SLIM source must be 1 byte–50 MiB')
        raw=p.read_bytes()
        bundle=adapt_slim_lifecycle(raw,scope=scope,subject_map=subject_map,checkpoint_id=checkpoint_id,
                                   record_prefix=record_prefix,demo=demo)
        result=self.methods.import_bundle(bundle,raw,p.name,scope)
        result.update({'adapter':'slim-lifecycle-v1','reported_only':True})
        return result
    def import_skill_creator_results(self,paths,*,scope,subject,checkpoint_id=None,record_prefix=None,
                                     feedback_role='unknown',demo=False):
        """Import grading/timing/benchmark files as reported regression evidence.

        An internal ZIP preserves every supplied source file byte for export;
        no paths are extracted or executed. No Lab evaluations are fabricated.
        """
        import io
        from pathlib import Path
        import zipfile
        from ..core import ValidationError
        from ..inspection.repository import MAX_FILE_BYTES
        from ..methods.skill_adapters import adapt_skill_creator_results
        if not isinstance(paths,(list,tuple)) or not 1<=len(paths)<=3:
            raise ValidationError('Provide 1–3 report paths')
        documents={}
        for value in paths:
            p=Path(value)
            if p.name not in ('grading.json','timing.json','benchmark.json') or p.name in documents:
                raise ValidationError('Report filenames must be unique grading.json/timing.json/benchmark.json')
            if not 0<p.stat().st_size<=MAX_FILE_BYTES:
                raise ValidationError('Report source must be 1 byte–50 MiB')
            documents[p.name]=p.read_bytes()
        bundle=adapt_skill_creator_results(documents,scope=scope,subject=subject,checkpoint_id=checkpoint_id,
            record_prefix=record_prefix,feedback_role=feedback_role,demo=demo)
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,raw in sorted(documents.items()):
                entry=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
                entry.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(entry,raw)
        result=self.methods.import_bundle(bundle,stream.getvalue(),'skill_creator_reports.zip',scope)
        result.update({'adapter':'skill-creator-results-v1','reported_only':True,'source_files':sorted(documents)})
        return result
    def register_evaluation_run(self,**record):
        from ..core import ValidationError
        if record.get('run_type','evaluation') != 'evaluation':
            raise ValidationError('register_evaluation_run requires run_type=evaluation')
        return self.store.register_run(dict(record,run_type='evaluation'))

    def _import_opd_source(self,path,adapter,options):
        """Retain exact source bytes with adapter-specific explicit scope."""
        from pathlib import Path
        from ..core import ValidationError
        from ..inspection.repository import MAX_FILE_BYTES
        p=Path(path)
        if not 0<p.stat().st_size<=MAX_FILE_BYTES:
            raise ValidationError('OPD source must be 1 byte–50 MiB')
        raw=p.read_bytes()
        bundle=adapter(raw,**options)
        result=self.methods.import_bundle(bundle,raw,p.name,options['scope'])
        manifest=bundle['objects'][0]
        result.update({'adapter':manifest['provenance']['adapter'],'reported_only':True,
                       'coverage':manifest['data']['coverage']})
        return result

    def import_verl_generations(self,path,*,scope,purpose,**options):
        """Import native rollout/validation JSONL as explicitly purposed evidence.

        score is not automatically EM; this does not create a formal evaluation,
        reconstruct token arrays, or certify that teacher hints were withdrawn.
        """
        from ..methods.opd_adapters import adapt_verl_generations
        return self._import_opd_source(path,adapt_verl_generations,dict(options,scope=scope,purpose=purpose))

    def import_ocsd_diagnostics(self,path,*,scope,source_format=None,**options):
        """Import OCSD Parquet (optional pyarrow) or a lossless JSONL row export.

        Native summaries remain step aggregates; selection meaning and possible
        max_samples truncation are preserved. No teacher prompt is invented.
        """
        from pathlib import Path
        from ..methods.opd_adapters import adapt_ocsd_diagnostics
        if source_format is None:
            source_format='parquet' if Path(path).suffix.lower()=='.parquet' else 'jsonl'
        return self._import_opd_source(path,adapt_ocsd_diagnostics,dict(options,scope=scope,source_format=source_format))

    def import_agentopsd_debug(self,path,*,scope,**options):
        """Import native SAVE_CGTD_DEBUG token JSONL without retokenizing."""
        from ..methods.opd_adapters import adapt_agentopsd_debug
        return self._import_opd_source(path,adapt_agentopsd_debug,dict(options,scope=scope))

    def import_opd_metrics(self,path,*,scope,source_family,**options):
        """Import a saved veRL or UniOPSD scalar dictionary with exact definitions.

        In particular UniOPSD opsd_rescue_frac is credit coverage, not task rescue.
        """
        from ..methods.opd_adapters import adapt_opd_metrics
        return self._import_opd_source(path,adapt_opd_metrics,dict(options,scope=scope,source_family=source_family))

    def _import_entropy_source(self,path,adapter,options):
        """Archive original bytes and import only explicitly reported evidence."""
        from pathlib import Path
        from ..core import ValidationError
        from ..inspection.repository import MAX_FILE_BYTES
        p=Path(path)
        if not 0<p.stat().st_size<=MAX_FILE_BYTES:
            raise ValidationError('Entropy source must be 1 byte–50 MiB')
        raw=p.read_bytes()
        bundle=adapter(raw,**options)
        result=self.methods.import_bundle(bundle,raw,p.name,options['scope'])
        manifest=bundle['objects'][0]
        result.update({'adapter':manifest['provenance']['adapter'],'reported_only':True,
                       'coverage':manifest['data']['coverage']})
        return result

    def import_entropy_metrics(self,path,*,scope,source_family,**options):
        """Read saved veRL/RAGEN logger dictionaries, retaining exact names.

        This does not recover removed token tensors or execute MI cross-scoring.
        Missing runtime temperature, support and producer revision stay unknown.
        """
        from ..methods.entropy_adapters import adapt_entropy_metrics
        return self._import_entropy_source(path,adapt_entropy_metrics,
            dict(options,scope=scope,source_family=source_family))

    def import_entropy_rows(self,path,*,scope,definition_id,**options):
        """Read explicit normalized observation/probe rows from producer hooks.

        ARPO/SSRL internal values require such a hook; their public code does
        not imply a universal entropy JSONL exporter. Definitions are explicit.
        """
        from ..methods.entropy_adapters import adapt_entropy_rows
        return self._import_entropy_source(path,adapt_entropy_rows,
            dict(options,scope=scope,definition_id=definition_id))

    def import_seal_scores(self,path,*,scope,definition_id,**options):
        """Read an explicitly serialized SEAL score_text result.

        SEAL normally writes HTML. This accepts its actual return-array names,
        with a separately supplied prefix/tokenizer/mask protocol; no inference.
        """
        from ..methods.entropy_adapters import adapt_seal_scores
        return self._import_entropy_source(path,adapt_seal_scores,
            dict(options,scope=scope,definition_id=definition_id))
    def register_model_snapshot(self,**record):return self.store.register_model_snapshot(record)
    def register_harness_run(self,**record):
        from ..core import ValidationError
        if record.get('run_type','harness_check') != 'harness_check':
            raise ValidationError('register_harness_run requires run_type=harness_check')
        return self.store.register_run(dict(record,run_type='harness_check'))
    def register_agent_evaluation(self,record=None,**fields):
        return self.repo.register_agent_evaluation(record if record is not None else fields)
    def start_evaluation(self,record=None,**fields):
        return self.store.start_evaluation(record if record is not None else fields)
    def log_trajectory(self,evaluation_id,record):return self.store.log_trajectory(evaluation_id,record)
    def finish_evaluation(self,evaluation_id,status='completed'):return self.store.finish_evaluation(evaluation_id,status)
    def log_harness_check(self,record):return self.repo.record_harness_check(record)
    def check_harness(self,*,id,run_id,harness_version,fixtures,handlers,evaluation_id=None,name=None,harness_fingerprint=None):
        from .evaluation import execute_contract_fixtures
        checks=execute_contract_fixtures(fixtures,handlers)
        return self.repo.record_harness_check({'id':id,'name':name or id,'run_id':run_id,
            'evaluation_id':evaluation_id,'harness_version':harness_version,'harness_fingerprint':harness_fingerprint,
            'checks':checks,'execution':'local_contract_fixtures','runner_model_requests':0,
            'limitation':'The runner invokes supplied handlers only; handler side effects are not inspected. Fixture results are not a model capability score.'})
