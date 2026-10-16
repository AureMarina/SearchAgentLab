"""Optional native OCSD Parquet roundtrip, separate from dependency-free tests.

Run on a collector with pyarrow installed:
    python tests/verify_opd_parquet_v095.py --report /tmp/opd-parquet-report.json

All rows are synthetic and all writes use a new temporary workspace.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def verify():
    import pyarrow as pa
    import pyarrow.parquet as parquet
    from searchagent_lab.studio.sdk import StudioRecorder
    rows = [{"global_step": 12, "batch_idx": i, "traj_uid": "fixture-" + str(i), "uid": "fixture-group",
        "step_index": 2, "available_future_len": 1, "future_obs_text_1": "合成观察",
        "num_tokens": 3 if i == 0 else 0, "step_selected": True,
        "is_nll_selected": False if i == 0 else None, "advantage": -1.0 if i == 0 else 0.0,
        "step_delta_F": 0.8 if i == 0 else 0.0, "step_delta_A": 0.3 if i == 0 else 0.0,
        "step_residual_e": 0.5 if i == 0 else 0.0} for i in range(2)]
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "step_000012.parquet"
        parquet.write_table(pa.Table.from_pylist(rows), source)
        raw = source.read_bytes()
        with StudioRecorder(root / "data") as studio:
            studio.store.register_project({"id": "p", "name": "Synthetic", "demo": True})
            studio.store.register_experiment({"id": "e", "project_id": "p", "name": "Synthetic", "demo": True})
            studio.store.register_run({"id": "r", "experiment_id": "e", "name": "Synthetic", "demo": True})
            result = studio.import_ocsd_diagnostics(source, scope={"run_id": "r"},
                record_prefix="parquet-fixture", source_population_size=8, source_complete=False, demo=True)
            saved = studio.methods.conn.execute("SELECT content FROM imports WHERE id=?", (result["import_id"],)).fetchone()[0]
            assert saved == raw
            checks.append("Original Parquet bytes retained exactly")
            assert studio.methods.object("parquet-fixture-source-0")["data"] == rows[0]
            assert studio.methods.object("parquet-fixture-source-1")["data"] == rows[1]
            checks.append("Arrow row and nullable NLL flag roundtrip")
            first = studio.methods.object("parquet-fixture-diagnostic-0")["data"]
            assert first["observation"]["has_future_observation"] is True
            assert first["observation"]["is_nll_selected"] is False
            assert "tokens" not in first
            checks.append("Future observation and NLL selection remain distinct, no token vectors invented")
            second = studio.methods.opd_record("parquet-fixture-diagnostic-1")["diagnostic"]
            assert all(m["value"] is None and m["reported_value"] == 0 for m in second["reported_metrics"])
            checks.append("Zero-token summaries retain raw zero with no valid support")
            assert result["coverage"]["source_complete"] is False
            assert result["coverage"]["source_population_size"] == 8
            checks.append("Upstream truncation and population coverage remain explicit")
            assert studio.store.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 0
            checks.append("Native report does not create formal evaluation outcomes")
    return {"passed": True, "synthetic_only": True, "pyarrow_version": pa.__version__,
        "check_count": len(checks), "checks": checks, "source_bytes": len(raw),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "dependency_scope": "Optional collector-only Parquet import; ordinary viewer/wheel has no runtime dependencies"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    result = verify()
    Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
