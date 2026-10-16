# Run package / center protocol v1

## Scope and version

`format = searchagent-lab.run/v1`; producer release is 0.7.0. Transport is an immutable ZIP containing one self-contained snapshot of one explicitly classified source run, not an arbitrary database merge. An unchanged recorded state reuses its package ID and revision. New content advances that run's revision.

Physical layout implemented by the exporter:

```text
manifest.json
records.json
inspection/<content-addressed-id>.<json|jsonl>
method_sources/<sha256>.bin
artifacts/<sha256>.bin
```

`records.json` contains project, experiment, profile, run, panels, checkpoints, metrics, evaluations, trajectories, batches, methods, inspection_files, method_sources, experiment_notes, artifacts and coverage. Empty sections remain explicit arrays. This maps directly to existing 0.6.0 validators; there is no invented second trajectory schema and no loss/reward renaming.

## Identity and trust

Manifest carries source workspace UUID, source-local run/project/experiment IDs, main/reproduction track, monotonically increasing revision, snapshot creation time, producer version, source labels, file inventory and SHA256 values. Each descriptor includes relative path, size and SHA256. `state_sha256` hashes canonical `{files, source}`; `package_id` hashes the canonical manifest excluding its own ID.

Hashing uses UTF-8 JSON, sorted object keys, compact separators and finite JSON values. The inventory is sorted by file path. ZIP metadata timestamps are not part of logical identity. Hashes show byte consistency, **not authenticated remote execution**, model honesty, correct scoring, or absence of cherry-picking. Authenticated submitter and self-reported operator/platform remain distinct facts.

Center global IDs for run, checkpoint, evaluation, trajectory, method and batch use `remote-<kind>-<sha256([source_uuid, kind, original_id])[:32]>`. An independent repeat must use a fresh source or run identity even when seed and display name match. Projects, experiments and frozen panels are centrally approved shared definitions, not automatically namespaced per user. Importing two incompatible definitions with the same ID is an explicit conflict.

Typed relationships are relocated, including run ownership, checkpoint/evaluation/trajectory ownership, documented method dependencies, evaluation bindings, training-update output checkpoints, batch method references and logged batch output checkpoint references. Inspector files are content-addressed and keep their original IDs. Method/traj reference digests are checked against source content before relocation and recomputed by the center validators; original reference digests remain as provenance. Free-form model output, tool response strings and raw source packages are not rewritten.

## Transaction and delivery

The center serializes imports under its local lock. The original verified ZIP and explicit artifact blobs are durably written before a database receipt can be committed. Existing main and sidecar SQLite databases participate in one attached transaction using local rollback journals (`DELETE`) and `synchronous=FULL`. Inner repository commits cannot partially commit the package. If late reference validation fails, run, score, method, inspector, batch and profile writes roll back together. Unreferenced immutable filesystem blobs can remain after a failed import; they are not accepted results.

A machine-level process lock excludes simultaneous center instances and center CLI ingestion. A marked center rejects legacy local Store writers. This is a single local-disk writer design, not shared SQLite across hosts, not WAL-based multi-file atomicity, not a PostgreSQL distributed service. Maintain all databases and journals on the same persistent local filesystem; do not run separate 0.6.0 processes against the center.

The ingestion API is `POST /api/team/packages`, `Content-Type: application/zip`, one known `Content-Length`. Clients use a scoped Bearer credential. Administrators may supply `X-Team-Owner` when importing on behalf of a collaborator; uploaders cannot impersonate another owner. A plan is approved through `POST /api/team/plan` or the stopped-center CLI. Original write routes are denied except three explicit admin-only project/experiment/profile registration routes.

- `accepted`: this snapshot committed; response identifies package ID, global run ID, revision, receipt time and declared evidence state.
- `duplicate`: exact logical package already accepted, no extra score or run.
- `superseded`: a newer snapshot is already stored; an unaccepted older one is not projected over it.
- `409` or semantic rejection: immutable identity/value conflict; existing records stay unchanged. A rejection report is retained when a valid manifest was available.

A newer snapshot must retain accepted immutable records and metric keys/values. It may append records or add distinct metrics at a step, not delete trajectories, overwrite values, change experiment purpose, reopen finished evaluations or rewrite a terminal run state. Adding a fresh evaluation requires a fresh evaluation ID. Concurrent shards are not automatically concatenated into one evaluation.

## Three independent states

`run_status` is the state last declared by the source. `sync_status=received_snapshot` only says the center accepted a particular snapshot. `evidence_status` is `declared_records_received`, `required_artifacts_pending`, or a detected missing archived-file condition. None of these is a continuously checked remote job heartbeat.

`coverage.kind=all_currently_recorded_records_for_run` means records actually present in the source databases at collection time. It does not mean every training rollout was logged. Inspector originals and method sources are included only when reached through typed references. Large-scale rollout sampling must be explained by the producer; the receiver does not infer it.

`storage=embedded` denotes actual archived bytes; `external_unverified` denotes a durable URI without verified center access; `missing` denotes a registered local file that has disappeared. Required non-embedded artifacts leave an explicit incomplete status. If a source file disappears after its bytes reached the center, the center keeps the archived original. The run detail listing checks file existence/size; downloads also check SHA256. No background S3/object-store fetch or remote availability promise is made.

## Collector guarantees and limits

The independent collector exports, fsyncs a ZIP into its disk outbox, then sends. Network errors preserve the exact package for retry with exponential backoff; transient 5xx/408/429 retry, most 4xx become blocked for explicit investigation. No success state is recorded unless the acknowledgement matches submitted ID/revision and an accepted delivery state. Credentials never follow HTTP redirects. Remote transport requires HTTPS; HTTP is allowed only to loopback for local/SSH use.

The original local workspace survives network failure. Disk exhaustion, device loss or a damaged local database can still lose data; the outbox is not a substitute for backup. By default it holds at most 1 GiB of retained ZIPs, reserves 64 MiB free disk, and keeps acknowledged files until explicit prune. The center presently has no automatic garbage collection or disk-quota controller.

Safety bounds: 128 MiB compressed ZIP, 96 MiB per member/records JSON, 4 MiB manifest, 512 MiB declared expanded total, 20,000 members; stored/deflated ZIP members only. Absolute/traversing/backslash paths, duplicate members, symlinks, encrypted members, duplicate JSON keys and non-finite JSON are rejected. No archive is extracted and no pickle, shell script or uploaded code is executed.

## Not included in v1

No per-event delta stream, chunk-resume, direct weights download, GPU task scheduling, shared online editing, organizational SSO, per-project viewer permissions, completed evaluation shard merge, baseline-ID migration across runs, cross-run method dependency closure, or bulk transfer of saved-report/diagnostic/manual-review sidecars. Unsupported cross-run typed dependencies fail export/import instead of becoming silent broken links. Experiment notes and original method-source bytes are preserved in the original package but are not recreated as center editing/history objects. Published reference numbers are not transformed into measured results.

Code/environment collection remains producer-side: the optional run registration SDK records an allowlisted runtime snapshot, while actual model/config/retriever/scorer/hardware facts must be supplied by the real runner. Sharing a panel or protocol hash does not demonstrate identical hardware, numerical determinism, matched training budgets, or fair latency comparisons.
