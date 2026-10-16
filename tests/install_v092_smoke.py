"""Build and verify the 0.9.2 wheel entirely offline, in an isolated venv.

Run after documentation and compiled frontend assets are frozen:
    python tests/install_v092_smoke.py

The example and server use a new temporary workspace. No original data, Node,
GPU, model endpoint, external package index or browser is needed at runtime.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email import message_from_bytes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.9.2"
WHEEL_NAME = f"searchagent_lab-{VERSION}-py3-none-any.whl"


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-build", action="store_true", help="Verify the existing final wheel without rebuilding it")
    args = parser.parse_args()
    checks, commands = [], []
    started = stamp()
    output = ROOT / "docs/v092/wheel-acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"version": VERSION, "started_at": started, "python": sys.version,
               "platform": sys.platform, "build_backend": "setuptools " + importlib.metadata.version("setuptools"),
               "scope": "Offline wheel installation; installed SDK; synthetic local example; loopback HTTP; packaged frontend assets",
               "uses_original_workspace": False, "gpu_or_model_execution": False,
               "runtime_requires_node": False, "checks": checks, "commands": commands}

    def check(name, condition, detail=None):
        entry = {"name": name, "passed": bool(condition)}
        if detail is not None: entry["detail"] = detail
        checks.append(entry)
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        if not condition: raise AssertionError(name)

    def run(argv, *, cwd, env, timeout=60, expect_success=True):
        proc = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
        commands.append({"command": list(map(str, argv)), "returncode": proc.returncode,
                         "stdout": proc.stdout[-12000:], "stderr": proc.stderr[-8000:]})
        if expect_success and proc.returncode:
            raise RuntimeError(proc.stderr or proc.stdout)
        return proc

    wheel = ROOT / "dist" / WHEEL_NAME
    try:
        build_env = dict(os.environ)
        build_env.pop("PYTHONPATH", None)
        build_env.update(PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONDONTWRITEBYTECODE="1")
        if not args.no_build:
            wheel.parent.mkdir(parents=True, exist_ok=True)
            run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
                 "--wheel-dir", wheel.parent, ROOT], cwd=ROOT, env=build_env, timeout=90)
        check("versioned wheel built without package-index access", wheel.is_file())
        receipt["wheel"] = {"name": wheel.name, "bytes": wheel.stat().st_size,
                            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        with zipfile.ZipFile(wheel) as archive:
            members = set(archive.namelist())
            metadata_name = f"searchagent_lab-{VERSION}.dist-info/METADATA"
            metadata = message_from_bytes(archive.read(metadata_name))
            check("wheel version metadata is 0.9.2", metadata["Version"] == VERSION)
            dependencies = metadata.get_all("Requires-Dist", [])
            receipt["requires_dist"] = dependencies
            check("wheel has zero runtime package dependencies", dependencies == [])
            readme = (ROOT / "README_ZH.md").read_text(encoding="utf-8")
            check("wheel embeds the frozen current README", metadata.get_payload(decode=True).decode("utf-8").strip() == readme.strip())
            receipt["readme_sha256"] = hashlib.sha256(readme.encode()).hexdigest()
            check("wheel includes evaluation views and the independent SDK",
                  {"searchagent_lab/studio/evaluation.py", "searchagent_lab/studio/sdk.py"}.issubset(members))
            prefix = "searchagent_lab/static/"
            manifest = json.loads(archive.read(prefix + "assets/build-manifest.json"))
            expected_assets = ["assets/" + f["name"] for f in manifest["files"]]
            asset_sizes = {"assets/" + f["name"]: f["bytes"] for f in manifest["files"]}
            check("all compiled build-manifest assets are in the wheel",
                  all(prefix + asset in members and len(archive.read(prefix + asset)) == asset_sizes[asset]
                      for asset in expected_assets), {"asset_count": len(expected_assets)})
            html = archive.read(prefix + "index.html").decode()
            html_assets = sorted(set(re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', html)))
            check("compiled index references packaged local assets",
                  bool(html_assets) and all(prefix + url.lstrip("/") in members for url in html_assets),
                  {"references": html_assets})
            check("shipped index has the current initial version label", 'id="s-version">0.9.2<' in html)

        with tempfile.TemporaryDirectory(prefix="searchagent-v092-install-") as directory:
            temporary = Path(directory)
            environment = temporary / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            binary_dir = environment / ("Scripts" if os.name == "nt" else "bin")
            python = binary_dir / ("python.exe" if os.name == "nt" else "python")
            runtime_env = dict(build_env)
            runtime_env.update(PATH=str(binary_dir), PYTHONNOUSERSITE="1")
            run([python, "-I", "-m", "pip", "install", "--no-index", "--no-deps", wheel],
                cwd=temporary, env=runtime_env)
            check("clean venv installs the wheel offline", True)
            installed_version = run([python, "-I", "-m", "searchagent_lab", "--version"],
                                    cwd=temporary, env=runtime_env).stdout.strip()
            check("installed CLI reports 0.9.2", installed_version == VERSION)
            probe = run([python, "-I", "-c", """import importlib.metadata,json,shutil
import searchagent_lab
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.studio.evaluation import EvaluationViews,execute_contract_fixtures
print(json.dumps({'module':searchagent_lab.__file__,'node':shutil.which('node'),
 'requires':importlib.metadata.requires('searchagent-lab') or [],
 'sdk':all(hasattr(StudioRecorder,k) for k in ('register_agent_evaluation','register_harness_run','check_harness'))}))
"""], cwd=temporary, env=runtime_env)
            info = json.loads(probe.stdout)
            check("installed imports are isolated from the source checkout",
                  str(environment) in info["module"] and str(ROOT) not in info["module"])
            check("installed SDK imports with no Node on PATH", info["node"] is None and info["sdk"] and info["requires"] == [])

            # The example inserts its own parent into sys.path. Copy only the
            # example and protocol to a clean folder, ensuring it imports the
            # installed wheel instead of accidentally testing the checkout.
            copied = temporary / "copied-example" / "examples"
            copied.mkdir(parents=True)
            for filename in ("evaluation_modes_example.py", "protocol_v2.synthetic.json"):
                shutil.copyfile(ROOT / "examples" / filename, copied / filename)
            workspace = temporary / "new-example-workspace"
            demo = run([python, "-I", copied / "evaluation_modes_example.py", "--data-dir", workspace],
                       cwd=temporary, env=runtime_env)
            demo_result = json.loads(demo.stdout)
            check("local example runs against the installed SDK in a new directory",
                  demo_result.get("verified") is True and demo_result.get("synthetic_only") is True)
            check("example verifies independent Agent and local Harness checks",
                  demo_result.get("independent_agent_has_no_training_step") is True and
                  demo_result.get("training_visible_without_panel") is True and
                  demo_result.get("harness_fixture_checks_passed") == 2)

            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0)); port = reserve.getsockname()[1]
            server_log = temporary / "server.log"
            with server_log.open("w+") as log:
                process = subprocess.Popen([str(python), "-I", "-m", "searchagent_lab", "serve", "--data-dir", str(workspace),
                                            "--host", "127.0.0.1", "--port", str(port)], cwd=temporary, env=runtime_env,
                                           stdout=log, stderr=log)
                base = "http://127.0.0.1:" + str(port)

                def fetch(path):
                    with urllib.request.urlopen(base + path, timeout=5) as response:
                        return response.read()

                def get(path): return json.loads(fetch(path))

                try:
                    for _ in range(80):
                        if process.poll() is not None:
                            log.seek(0); raise RuntimeError("Installed server exited: " + log.read())
                        try:
                            health = get("/api/health"); break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        raise RuntimeError("Installed server did not become healthy")
                    check("installed HTTP server starts without Node or network installation",
                          health.get("ok") is True and get("/api/session").get("version") == VERSION)
                    agent = get("/api/studio/evaluation-summary?run=agent-run-a&evaluation=agent-a")
                    check("installed Agent summary preserves model snapshots without training steps",
                          agent["purpose"]["value"] == "agent_eval" and agent["context"]["model"]["step"] is None and
                          agent["coverage"]["recorded"] == 2 and agent["training"]["has_training_evidence"] is False)
                    training = get("/api/studio/training-summary?run=fixture-training")
                    check("installed training summary returns real batch and checkpoint coverage",
                          training["batch_summary"]["sample_count"] == 3 and
                          training["coverage"]["evaluation_count"] == 2 and
                          training["classification"]["training_metric_rows"] == 1)
                    rollout = get("/api/studio/training-rollout?run=fixture-training&batch=fixture-batch&group=mixed-group&sample_index=1")
                    check("installed rollout endpoint uses an explicit sample index",
                          rollout["sample"]["id"] == "sample-negative" and rollout["sample"]["reward"] == 0 and
                          rollout["document"]["view"]["record_purpose"]["value"] == "train_rollout")
                    controlled = get("/api/studio/evaluation-pair?a=agent-a&b=agent-b&axis=harness")
                    rejected = get("/api/studio/evaluation-pair?a=agent-a&b=agent-b&axis=checkpoint")
                    check("installed comparison endpoint enforces the selected change axis",
                          controlled["compatible"] is True and controlled["rl_attribution"] is False and
                          rejected["compatible"] is False)
                    harness = get("/api/studio/evaluation-summary?run=fixture-checks")
                    check("installed Harness checks stay separate from evaluation scores",
                          harness["evaluations"] == [] and harness["checks"][0]["passed"] == 2)
                    installed_html = fetch("/").decode()
                    check("installed HTTP root serves the frozen compiled index", installed_html == html)
                    served_assets = set(expected_assets) | {url.lstrip("/") for url in html_assets}
                    check("all compiled assets are readable over installed HTTP",
                          all(len(fetch("/" + asset)) == asset_sizes[asset] for asset in sorted(served_assets)),
                          {"asset_count": len(served_assets), "entry": manifest["main"]})
                finally:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=5)
        for previous in wheel.parent.glob("*.whl"):
            if previous.name != WHEEL_NAME: previous.unlink()
        check("dist contains only the accepted 0.9.2 wheel", [p.name for p in wheel.parent.glob("*.whl")] == [WHEEL_NAME])
        receipt["passed"] = True
    except BaseException as error:
        receipt["passed"] = False
        receipt["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        receipt.update(finished_at=stamp(), check_count=len(checks), passed_count=sum(c["passed"] for c in checks))
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for generated in (ROOT / "build", ROOT / "searchagent_lab.egg-info"):
            if generated.is_dir() and not generated.is_symlink(): shutil.rmtree(generated)
    print(f"PASSED {len(checks)} offline wheel acceptance checks", flush=True)


if __name__ == "__main__":
    main()
