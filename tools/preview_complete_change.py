"""Optional native QA launcher for a wholly synthetic complete-change project.

The fresh temporary profile contains no production data/settings/credentials.
The fake provider returns one structural delta and one scripted passing review.
This proves native workflow wiring only, never model judgment or plan quality.
Default opens the native UI; --check proves the same backend flow without GUI.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from measure_complete_change import (
    LIMITS,
    MODE,
    ROOT,
    SYNTHETIC_INPUT,
    NoSecrets,
    OfflineGuard,
    capture_provider,
    collect,
    export_capture,
    request,
    synthetic_project,
    write_json,
)

LABEL = "SYNTHETIC · 完整改动 · 假模型 / 零真实请求"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Headless: exercise the genuine runtime")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "backend"))
    evidence = args.evidence_dir.resolve() if args.evidence_dir else None
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    guard = OfflineGuard(allow_loopback=True)
    guard.install()
    from evograph.application.api import Application
    from evograph.frontend_build import bundle_status

    with TemporaryDirectory(prefix="evograph-synthetic-complete-change-") as directory:
        profile = Path(directory)
        guard.register(profile)
        app = Application(profile, NoSecrets(guard))
        project = synthetic_project(app)
        project.name = LABEL
        project.description = "纯本机假模型：一次完整结构改动 + 一次假评审。保留原验收，增加 M1 对 M0 的前置与消费关系。不能证明真实模型质量。"
        project = app.db.save(project, "synthetic_label")
        calls = capture_provider(app, synthetic=True, delay=0 if args.check else max(args.delay, 0))
        ready, reason = bundle_status(ROOT, ROOT / "dist")
        app.operations["settings.get"] = lambda: {"adapters": [], "provider": {
            "adapter": "synthetic_local_only", "has_key": False,
            "config": {"model": "SYNTHETIC FAKE PROVIDER / ZERO REAL REQUESTS"}}}

        def forbidden(*args, **kwargs):
            raise RuntimeError("Synthetic preview refuses provider/credential configuration")

        app.operations["settings.save"] = forbidden
        app.operations["settings.test"] = forbidden
        app.operations["research.configure"] = forbidden
        app.operations["research.configure_search"] = forbidden
        app.operations["research.configure_vision"] = forbidden
        result = {"label": LABEL, "synthetic_only": True, "mode": MODE,
                  "limits": LIMITS, "profile_is_new_temporary": True,
                  "real_provider_dispatches": 0, "credential_accesses": 0,
                  "network_policy": "deny non-loopback", "frontend_verified": ready,
                  "frontend_status": reason, "gui_started": False, "input": SYNTHETIC_INPUT,
                  "instructions": "选择此 SYNTHETIC 项目。粘贴 input，选择完整改动有界模式并确认 1 阶段 / 2 次 / 393216 B。"}
        if args.check:
            events = collect(app, project, request(app, project, "offline-native-complete-check"), SYNTHETIC_INPUT)
            job = app.planning_jobs.store.latest(project.id)
            candidate = app.unified.store.latest(project.id)
            assert job["status"] == "applied" and len(calls) == 2, (job, events[-3:])
            result.update(job_status=job["status"], fake_provider_dispatches=len(calls),
                          canonical_revision=app.db.get(project.id).revision,
                          safety=guard.counts, headless_backend_only=True)
            if evidence:
                write_json(evidence / "headless-check.json", result)
                write_json(evidence / "headless-events.json", events)
                write_json(evidence / "headless-job.json", job)
                write_json(evidence / "headless-candidate.json", candidate)
                for index, capture in enumerate(calls, 1):
                    export_capture(evidence, f"headless-request-{index}", capture)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return
        if not ready:
            raise SystemExit(reason + "; build the frontend in this isolated worktree before native QA")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        import webview
        from evograph.transport.desktop import DesktopBridge, check_desktop_environment
        check_desktop_environment()
        os.environ.setdefault("QT_API", "pyside6")
        bridge = DesktopBridge(app)
        window = webview.create_window(LABEL, str(ROOT / "dist/index.html"), js_api=bridge,
                                      width=1480, height=960, min_size=(1050, 720))
        bridge._window = window
        if evidence:
            # Existing DesktopBridge remains the user-facing production transport.
            async def snapshot():
                while True:
                    await asyncio.sleep(1)
                    job = app.planning_jobs.store.latest(project.id)
                    if job and job["status"] in {"applied", "stopped", "cancelled"}:
                        write_json(evidence / "native-job.json", job)
                        write_json(evidence / "native-candidate.json", app.unified.store.latest(project.id))
                        for index, capture in enumerate(calls, 1):
                            export_capture(evidence, f"native-request-{index}", capture)
                        return

            def capture_terminal():
                asyncio.run(snapshot())

            webview.start(capture_terminal, http_server=True, gui="qt")
        else:
            webview.start(http_server=True, gui="qt")


if __name__ == "__main__":
    main()
