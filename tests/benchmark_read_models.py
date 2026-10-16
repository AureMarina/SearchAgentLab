"""Reproducible read-only local benchmarks; all training evidence is synthetic.

Example: python tests/benchmark_read_models.py methods --source-root /path/to/base
Run the same command against 0.8.0, on the same machine, after stopping other tests.
This measures repository calls, not HTTP or user-server/browser latency.
"""
from __future__ import annotations
import argparse
import json
import pathlib
import statistics
import sys
import tempfile
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('workload', choices=['methods', 'dynamics'])
    p.add_argument('--source-root', type=pathlib.Path, default=ROOT)
    p.add_argument('--archive', type=pathlib.Path, default=ROOT.parent/'eval-data/reference-assets/skillrl/SkillRL_skills_20261005.zip')
    p.add_argument('--trials', type=int, default=7)
    args = p.parse_args()
    if not 1 <= args.trials <= 100:
        p.error('--trials must be in 1..100')
    if not (args.source_root/'searchagent_lab').is_dir():
        p.error('--source-root must contain searchagent_lab/')
    sys.path[:0] = [str(args.source_root.resolve()), str((args.source_root/'examples').resolve())]
    from searchagent_lab import __version__

    def measure(fn):
        fn()  # One warm-up, excluded from the samples.
        samples = []
        for _ in range(args.trials):
            started = time.perf_counter()
            value = fn()
            samples.append((time.perf_counter()-started)*1000)
        return {'median_ms': statistics.median(samples), 'min_ms': min(samples),
                'max_sample_ms': max(samples), 'samples_ms': samples,
                'response_bytes': len(json.dumps(value, ensure_ascii=False).encode())}, value

    report = {'version': __version__, 'trials': args.trials, 'warmup': 1,
              'scope': 'local repository calls; not user-server latency; no GPU training'}
    if args.workload == 'methods':
        from searchagent_lab.methods.sdk import MethodRecorder
        from searchagent_lab.methods.schema import SCHEMA
        with zipfile.ZipFile(args.archive) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            banks = [{'id': 'bank-'+str(i), 'kind': 'skill_bank', 'data': json.loads(archive.read(f['file']))}
                     for i, f in enumerate(manifest['files'])]
        payload = 'Evidence-not-real-experiment-'+'x'*16000
        traces = [{'id': 'trace-'+str(i), 'kind': 'trace', 'data': {'text': payload}} for i in range(1000)]
        with tempfile.TemporaryDirectory() as tmp, MethodRecorder(tmp) as rec:
            rec.repo.import_bundle({'method_schema': SCHEMA, 'objects': banks+traces})
            report['fixture'] = {'traces': len(traces), 'trace_text_bytes': len(payload),
                                 'banks': len(banks), 'synthetic_performance_only': True}
            report['catalog'], _ = measure(lambda: rec.repo.catalog())
            report['bank'], _ = measure(lambda: rec.repo.skill_bank('bank-20'))
            report['details'], _ = measure(lambda: rec.repo.details('bank-20'))
    else:
        import copy
        from searchagent_lab.core import Store as CoreStore
        from searchagent_lab.studio.sdk import StudioRecorder
        from searchagent_lab.server import LabServer
        from seed_studio_demo import seed
        original = CoreStore.log_trajectory
        def enlarged(self, evaluation_id, record):
            data = copy.deepcopy(record)
            for step in data.get('steps', []):
                step['benchmark_padding'] = 'x'*32768
            return original(self, evaluation_id, data)
        CoreStore.log_trajectory = enlarged
        try:
            with tempfile.TemporaryDirectory() as tmp:
                seed(tmp)
                with StudioRecorder(tmp) as rec, LabServer(('127.0.0.1',0), rec.store) as server:
                    measurements, data = measure(lambda: server.studio.dynamics('studio-run','studio-synthetic-11'))
                    report.update(measurements)
                    row = rec.store.conn.execute('SELECT count(*),sum(length(data)) FROM trajectories').fetchone()
                    report['fixture'] = {'trajectories': row[0], 'raw_trajectory_bytes': row[1], 'synthetic': True}
                    report['scores'] = [c['evaluation']['stats']['answer_correct'] if c['evaluation'] else None for c in data['matrix']['columns']]
                    report['row_count'] = len(data['matrix']['rows'])
                    report['step_counts'] = [r['cells'][0]['step_count'] if r['cells'][0] else None for r in data['matrix']['rows']]
        finally:
            CoreStore.log_trajectory = original
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
