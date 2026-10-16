"""Small, synchronous logging hooks for method artifacts and actual training use.

This module executes no model, retriever, verifier, optimizer or network request.
Choose a local MethodRecorder sink (visible to the existing collector) or an
importer-compatible JSONL journal. Journal events receive shape validation;
typed references and experiment ownership are checked on repository import.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import threading
from typing import Any

from ..core import ValidationError
from ..inspection.repository import MAX_FILE_BYTES, digest, encode, strict_json
from ..team.util import durable_write, process_lock, sync_dir
from .schema import MAX_OBJECTS, SCHEMA, identifier, validate_object
from .sdk import MethodRecorder


class MethodEvidenceRecorder:
    """Record immutable method events in one explicitly selected run scope.

    ``data_dir`` records through the existing MethodRecorder immediately. Parents
    must exist or be included in the same ``record_batch``. ``journal_path``
    appends native method JSONL, without creating any database; references can
    point to earlier events or to other evidence supplied at import time.

    Use globally unique event IDs, normally prefixed by the actual run ID. The
    same ID with identical contents is idempotent; changes need a new ID. A local
    journal has one writer at a time. Give distributed workers distinct paths.
    Unknown facts are omitted or null, never inferred from method names.
    """

    def __init__(self, *, scope: dict, data_dir=None, journal_path=None,
                 usage: str = "unknown", demo: bool = False, provenance: dict | None = None):
        if (data_dir is None) == (journal_path is None):
            raise ValueError("Provide exactly one of data_dir or journal_path")
        if not isinstance(scope, dict) or not scope or set(scope) - {"project_id", "experiment_id", "run_id"}:
            raise ValidationError("Explicit project/experiment/run scope is required")
        for key, value in scope.items():
            identifier(value, key)
        if not isinstance(demo, bool):
            raise ValidationError("demo must be boolean")
        if provenance is not None and not isinstance(provenance, dict):
            raise ValidationError("provenance must be a JSON object")
        self.scope = copy.deepcopy(scope)
        self.usage = usage
        self.demo = demo
        self.provenance = copy.deepcopy(provenance)
        self._lock = threading.RLock()
        self._closed = False
        self._records: dict[str, dict] = {}
        self._local_ids: dict[str, None] = {}
        self._journal_lock = None
        self._recorder = None
        self.journal_path = Path(journal_path).expanduser().resolve() if journal_path is not None else None
        # Validate constructor defaults before touching either sink.
        self._prepare({"id": "recorder-shape-check", "kind": "trace", "data": {}})
        try:
            if self.journal_path is None:
                self._recorder = MethodRecorder(data_dir)
            else:
                self.journal_path.parent.mkdir(parents=True, exist_ok=True)
                self._journal_lock = process_lock(self.journal_path.with_name(self.journal_path.name + ".lock"))
                self._journal_lock.__enter__()
                self._read_journal()
        except BaseException:
            self.close()
            raise

    def _read_journal(self) -> None:
        if not self.journal_path.exists():
            return
        if self.journal_path.stat().st_size > MAX_FILE_BYTES:
            raise ValidationError("Journal exceeds the importer's 50 MiB limit; use a new shard")
        raw = self.journal_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ValidationError("Journal has an incomplete final line; preserve it and recover to a new shard")
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                envelope = strict_json(line)
                if not isinstance(envelope, dict) or set(envelope) != {"method_schema", "object"} or envelope["method_schema"] != SCHEMA:
                    raise ValidationError("Expected method_schema and object")
                original = envelope["object"]
                event = self._prepare(original)
                if event != original:
                    raise ValidationError("Journal scope/defaults differ from this recorder")
                if event["id"] in self._records:
                    raise ValidationError("Duplicate event ID in journal")
                self._records[event["id"]] = event
            except (ValueError, TypeError, KeyError) as exc:
                raise ValidationError(f"Invalid method journal line {line_number}: {exc}") from exc
        if len(self._records) > MAX_OBJECTS:
            raise ValidationError("Journal exceeds 10,000 events; use a new shard")

    def _prepare(self, event: dict) -> dict:
        if not isinstance(event, dict):
            raise ValidationError("Event must be a JSON object")
        result = copy.deepcopy(event)
        if "scope" in result and result["scope"] != self.scope:
            raise ValidationError("Event scope differs from its recorder")
        if "demo" in result and result["demo"] != self.demo:
            raise ValidationError("Event demo status differs from its recorder")
        result["scope"] = copy.deepcopy(self.scope)
        result["demo"] = self.demo
        result.setdefault("usage", self.usage)
        if "provenance" in result and not isinstance(result["provenance"], dict):
            raise ValidationError("provenance must be a JSON object")
        if self.provenance is not None:
            result.setdefault("provenance", copy.deepcopy(self.provenance))
        validate_object(result)
        return result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._recorder is not None:
                    self._recorder.close()
            finally:
                if self._journal_lock is not None:
                    self._journal_lock.__exit__(None, None, None)
                    self._journal_lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def content_sha256(content: Any) -> str:
        """SHA256 of the same finite, canonical JSON used by method imports.

        This hashes recorded JSON, not unknown remote bytes or model weights.
        """
        try:
            return digest(content)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValidationError("Hash content must be finite JSON") from exc

    @staticmethod
    def evidence_ref(event: dict, pointer: str = "") -> dict:
        """Return an object reference bound to the full original data hash.

        ``pointer`` is a JSON Pointer into event['data']; import resolves it.
        """
        identifier(event.get("id"), "evidence object ID")
        if not isinstance(pointer, str):
            raise ValidationError("Evidence pointer must be text")
        return {"object_id": event["id"], "pointer": pointer, "sha256": digest(event["data"])}

    def record(self, record_id: str, kind: str, data: dict, **attributes) -> dict:
        """Log original JSON, with optional title/version/usage/source evidence.

        Accepted attributes are title, version, usage, derived_from,
        evidence_refs, shareable, and provenance. ``derived_from`` records
        provenance; it does not establish a causal contribution.
        """
        allowed = {"title", "version", "usage", "derived_from", "evidence_refs", "shareable", "provenance"}
        if set(attributes) - allowed:
            raise ValidationError("Unknown event attributes: " + ", ".join(sorted(set(attributes) - allowed)))
        result = self.record_batch([{"id": record_id, "kind": kind, "data": data, **attributes}])
        return result[0]

    def record_batch(self, events: list[dict]) -> list[dict]:
        """Validate a batch before writing; repository imports are atomic.

        A journal batch is appended and fsynced. If a process/disk failure leaves
        an incomplete line, reopening refuses it rather than hiding the damage.
        All records in an offline shard can be imported atomically afterwards.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Recorder is closed")
            if not isinstance(events, list) or not 1 <= len(events) <= MAX_OBJECTS:
                raise ValidationError("record_batch needs 1–10,000 events")
            prepared = [self._prepare(event) for event in events]
            ids = [event["id"] for event in prepared]
            if len(ids) != len(set(ids)):
                raise ValidationError("Duplicate event ID in batch")
            if self._recorder is not None:
                # Keep payloads in the existing database, not in an unbounded
                # in-memory training callback cache. The repository owns retry
                # deduplication, immutable IDs and reference validation.
                self._recorder.record_batch(prepared, scope=self.scope, demo=self.demo)
                self._local_ids.update(dict.fromkeys(ids))
                return copy.deepcopy(prepared)
            pending = []
            for event in prepared:
                previous = self._records.get(event["id"])
                if previous is not None:
                    if previous != event:
                        raise ValidationError("Immutable event ID has different contents: " + event["id"])
                else:
                    pending.append(event)
            if pending:
                if len(self._records) + len(pending) > MAX_OBJECTS:
                    raise ValidationError("Journal exceeds 10,000 events; use a new shard")
                payload = b"".join((encode({"method_schema": SCHEMA, "object": event}) + "\n").encode("utf-8") for event in pending)
                old_size = self.journal_path.stat().st_size if self.journal_path.exists() else 0
                if old_size + len(payload) > MAX_FILE_BYTES:
                    raise ValidationError("Journal exceeds the importer's 50 MiB limit; use a new shard")
                with self.journal_path.open("ab") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if old_size == 0:
                    sync_dir(self.journal_path.parent)
            for event in pending:
                self._records[event["id"]] = event
            return copy.deepcopy(prepared)

    def export_bundle(self, path=None) -> dict:
        """Export events seen by this recorder, without crawling dependencies.

        Journal mode includes all events already present when reopened. A local
        sink exports only this recorder session's events; existing referenced
        objects are exported by the standard per-run result package or by
        MethodRepository.export. A preexisting output file is never overwritten.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Recorder is closed")
            if self._recorder is not None:
                events = [self._recorder.repo._raw_object(record_id) for record_id in self._local_ids]
            else:
                events = copy.deepcopy(list(self._records.values()))
            if not events:
                raise ValidationError("No events to export")
            bundle = {"method_schema": SCHEMA, "scope": copy.deepcopy(self.scope), "demo": self.demo,
                      "objects": events,
                      "description": "Recorded method evidence. Links establish provenance, not causal contribution. No agent or training was executed by this recorder."}
            if path is not None:
                raw = (encode(bundle) + "\n").encode("utf-8")
                if len(bundle["objects"]) > MAX_OBJECTS or len(raw) > MAX_FILE_BYTES:
                    raise ValidationError("Bundle exceeds import limits; export per-run packages or smaller shards")
                durable_write(Path(path).expanduser().resolve(), raw, exclusive=True)
            return bundle

    # The data dictionaries below deliberately use the import schema verbatim.
    # Adapters retain method-specific fields rather than normalizing them away.
    def method_profile(self, record_id, *, method_name, families, configuration=None, skill_mode=None, **attributes):
        data = {"method_name": method_name, "families": families,
                "configuration": {} if configuration is None else copy.deepcopy(configuration)}
        if skill_mode is not None:
            data["skill_mode"] = skill_mode
        return self.record(record_id, "method_profile", data, **attributes)

    def trace(self, record_id, data, **attributes):
        return self.record(record_id, "trace", data, **attributes)

    def skill_bank(self, record_id, content, *, version, **attributes):
        """Freeze the exact supplied bank JSON; a changed bank needs a new ID."""
        return self.record(record_id, "skill_bank", content, version=version, **attributes)

    def skill_asset(self, record_id, data, **attributes):
        """Freeze a complete package version and compute its canonical JSON hash.

        ``data`` contains skill_id, version, and package.files. Files contain
        actual UTF-8 text or explicitly encoded base64 bytes, not mutable paths.
        An explicitly supplied package_hash must match. The repository also
        enforces version identity, parent links and scoped ownership.
        """
        if not isinstance(data, dict) or not isinstance(data.get("package"), dict):
            raise ValidationError("skill_asset requires a package JSON object")
        payload = copy.deepcopy(data)
        computed = self.content_sha256(payload["package"])
        if payload.get("package_hash", computed) != computed:
            raise ValidationError("Skill package hash does not match recorded JSON")
        payload["package_hash"] = computed
        return self.record(record_id, "skill_asset", payload, **attributes)

    def skill_instance(self, record_id, data, **attributes):
        """Record one observed use with an asset, legacy member, or inline text.

        Inline text gets its content hash; no external bank is required. This
        helper does not infer visibility, loading, execution, selection source,
        an end boundary, or contribution. Supply observed evidence explicitly.
        """
        if not isinstance(data, dict):
            raise ValidationError("skill_instance data must be a JSON object")
        payload = copy.deepcopy(data)
        inline = payload.get("inline_skill")
        if isinstance(inline, dict) and "text" in inline:
            computed = self.content_sha256(inline["text"])
            if inline.get("content_hash", computed) != computed:
                raise ValidationError("Inline skill hash does not match recorded text")
            inline["content_hash"] = computed
        return self.record(record_id, "skill_instance", payload, **attributes)

    def skill_evolution(self, record_id, data, **attributes):
        """Record a scoped asset/bank/instance decision without changing history."""
        return self.record(record_id, "skill_evolution", data, **attributes)

    def skill_comparison(self, record_id, data, **attributes):
        """Declare exact matrix/ablation evaluations; omitted cells stay untested.

        Declaration executes no evaluation and does not establish gain. The
        comparison view checks frozen model, skill state and protocol evidence.
        """
        return self.record(record_id, "skill_comparison", data, **attributes)

    def skill_retrieval(self, record_id, data, *, final_input=None, **attributes):
        """Record candidates, selection, and *actual* final rendered injections.

        Provide final_input only after prompt assembly/truncation, with the exact
        messages sent to the model. Its JSON hash is computed, not its adoption.
        None leaves input evidence unknown; it does not imply an empty request.
        """
        if not isinstance(data, dict):
            raise ValidationError("skill_retrieval data must be a JSON object")
        payload = copy.deepcopy(data)
        if final_input is not None:
            if "final_input" in payload and payload["final_input"] != final_input:
                raise ValidationError("Conflicting final model inputs")
            payload["final_input"] = copy.deepcopy(final_input)
            computed = self.content_sha256(final_input)
            if payload.get("final_input_sha256", computed) != computed:
                raise ValidationError("Final input hash does not match recorded JSON")
            payload["final_input_sha256"] = computed
        return self.record(record_id, "skill_retrieval", payload, **attributes)

    def selfplay_cycle(self, record_id, data, **attributes):
        """Retain the recorded round, role revisions and supply-stage coverage."""
        return self.record(record_id, "selfplay_cycle", data, **attributes)

    def selfplay_reference(self, record_id, data, **attributes):
        """Freeze the reference solver and its full probe protocol.

        This records a protocol; it does not execute attempts or fill missing
        solver, harness, retrieval, sampling or verifier identities.
        """
        return self.record(record_id, "selfplay_reference", data, **attributes)

    def selfplay_pool_event(self, record_id, data, **attributes):
        """Record question admission/removal separately from actual training use."""
        return self.record(record_id, "selfplay_pool_event", data, **attributes)

    def selfplay_comparison(self, record_id, data, **attributes):
        """Record a same-experiment old-pool/fresh-supply comparison.

        Use an experiment-scoped recorder for cross-run arms. Qualification is
        derived from the referenced completed updates, fixed evaluations and
        recorded total budgets; arm labels alone are never evidence of gain.
        """
        return self.record(record_id, "selfplay_comparison", data, **attributes)

    def generated_question(self, record_id, data, **attributes):
        return self.record(record_id, "generated_question", data, **attributes)

    def proposal(self, record_id, data, **attributes):
        return self.record(record_id, "proposal", data, **attributes)

    def question_review(self, record_id, data, **attributes):
        """Retain verifier failure/error as well as accepted questions."""
        return self.record(record_id, "question_review", data, **attributes)

    def solver_attempt(self, record_id, data, **attributes):
        return self.record(record_id, "solver_attempt", data, **attributes)

    def training_use(self, record_id, data, **attributes):
        """Selected/sample/excluded and actual signal; never synthesizes update.

        SkillCredit belongs in credit_assignment with separate skill_decision
        and action_decision reward/advantage/mask/source. It is not bank utility.
        """
        return self.record(record_id, "training_use", data, **attributes)

    def training_update(self, record_id, data, **attributes):
        """Record after the actual callback; status must be explicit when known."""
        return self.record(record_id, "training_update", data, **attributes)

    def opd_failure(self, record_id, data, **attributes):
        """Freeze a failure reached by the student, including actual state.

        A captured state is recorded evidence, not a promise that this SDK
        restored it. See opd_recovery for same-state continuation comparisons.
        """
        return self.record(record_id, "opd_failure", data, **attributes)

    def opd_teacher(self, record_id, data, **attributes):
        """Keep teacher construction/context and rescore vs actual continuation.

        Supply teacher_revision, actual start/trace/verifier references, disclosed
        privileges and measured cost when available. Missing fields stay unknown.
        """
        return self.record(record_id, "opd_teacher", data, **attributes)

    def opd_supervision(self, record_id, data, **attributes):
        """Record the real objective/mask/weight, including zero or missing signal."""
        return self.record(record_id, "opd_supervision", data, **attributes)

    def opd_configuration(self, record_id, *, native_config, teacher=None,
                          source_revision=None, method_name="veRL native OPD", **attributes):
        """Record native switches without inferring a completed optimizer step.

        use_task_rewards and use_policy_gradient remain separate. The complete
        supplied configuration stays available alongside the extracted fields.
        """
        from .opd_adapters import verl_opd_configuration
        opd = verl_opd_configuration(native_config, teacher=teacher, source_revision=source_revision)
        return self.method_profile(record_id, method_name=method_name, families=["opd"],
                                   configuration={"opd": opd}, **attributes)

    def opd_diagnostic(self, record_id, data, **attributes):
        """Record group/step/token/distribution/observation evidence verbatim.

        Keep task and OPD signals/masks distinct. An aggregate or sampled-token
        logprob does not establish missing top-k support, update or task gain.
        """
        return self.record(record_id, "opd_diagnostic", data, **attributes)

    def opd_recovery(self, record_id, data, **attributes):
        """Declare exact failure-state before/teacher/after attempts and results.

        No continuation is executed. Missing/mismatched attempts stay visible;
        the comparison view checks actual start, context and verifier evidence.
        """
        return self.record(record_id, "opd_recovery", data, **attributes)

    def entropy_definition(self, record_id, data, **attributes):
        """Freeze the measured quantity, support, mask and aggregation.

        Changing a temperature, tokenizer, prefix protocol or estimator needs a
        new definition/observation identity. No model distribution is computed.
        """
        return self.record(record_id, "entropy_definition", data, **attributes)

    def entropy_observation(self, record_id, data, **attributes):
        """Record a nonnegative measurement or explicit missing observation.

        Fixed-prefix probes and on-policy state averages remain separate.
        Token arrays are supplied by the producer, never reconstructed here.
        """
        return self.record(record_id, "entropy_observation", data, **attributes)

    def entropy_probe(self, record_id, data, **attributes):
        """Record signed diagnostic proxies such as RAGEN MI or input tests.

        A prompt-identification score is not a Search retrieval hit rate.
        """
        return self.record(record_id, "entropy_probe", data, **attributes)

    def entropy_intervention(self, record_id, data, **attributes):
        """Keep entropy-guided selection and actual task/entropy signals.

        Record sampling with training_use and an actual completed callback with
        training_update separately. A configured coefficient is not an update.
        """
        return self.record(record_id, "entropy_intervention", data, **attributes)

    def entropy_comparison(self, record_id, data, **attributes):
        """Declare exact prefix, budget or estimator comparisons without runs.

        Controlled-comparison views check referenced evidence. Missing trials,
        prefixes, labels, costs or protocol identities remain visibly unknown.
        """
        return self.record(record_id, "entropy_comparison", data, **attributes)

    def entropy_configuration(self, record_id, *, native_config,
                              source_revision=None, method_name="Entropy + RLVR", **attributes):
        """Preserve actual veRL entropy switches without filling defaults."""
        from .entropy_adapters import verl_entropy_configuration
        configuration = verl_entropy_configuration(native_config, source_revision=source_revision)
        return self.method_profile(record_id, method_name=method_name, families=["entropy"],
                                   configuration={"entropy": configuration}, **attributes)

    def evaluation_binding(self, record_id, data, **attributes):
        """Bind observed method state to an existing evaluation, without scoring."""
        return self.record(record_id, "evaluation_binding", data, **attributes)
