"""Synthetic native preview using the real app and fake streams in a NEW temp DB.

No credentials/config/profile imports, no adapter/HTTP requests, loopback assets only.
Do not use this launcher with real projects. Fake reviews are not quality evidence.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
LABEL = "SYNTHETIC · 有界规划任务 · 假模型 / 零真实请求"


def guards(profile):
    def audit(event, args):
        if event == "sqlite3.connect" and not Path(str(args[0])).resolve().is_relative_to(profile):
            raise RuntimeError("Synthetic preview refuses every database outside its new temporary profile")
        if event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1", "localhost"}:
                raise RuntimeError("Synthetic preview permits loopback asset connections only")
        if event == "import" and (str(args[0]).split(".")[0] == "keyring" or (
                str(args[0]).startswith("evograph.providers.") and args[0] != "evograph.providers.base")):
            raise RuntimeError("Synthetic preview forbids credential managers and real adapters")
    sys.addaudithook(audit)


class NoSecrets:
    def get(self, name):
        raise RuntimeError("Synthetic preview forbids credential reads")

    def set(self, name, secret):
        raise RuntimeError("Synthetic preview forbids credential writes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests")]
    with TemporaryDirectory(prefix="evograph-synthetic-bounded-job-") as directory:
        profile = Path(directory).resolve()
        guards(profile)
        from evograph.application.api import Application
        from evograph.frontend_build import bundle_status
        from test_bounded_planning_job import INPUT, collect, provider, request, seven
        ready, reason = bundle_status(ROOT, ROOT / "dist")
        if not ready:
            raise SystemExit(reason + "; run npm run build in this isolated worktree")
        app = Application(profile, NoSecrets())
        project = seven.__wrapped__(app)
        project.name = LABEL
        project.description = "纯本机假模型演示。保留 5 个真实编译器检查点，再演示 9 次假调用跨阶段原子应用；不证明模型质量。"
        project = app.db.save(project, "synthetic_label")
        # Produce five genuine saved compiler checkpoints in a closed synthetic
        # predecessor, so retained/new accounting starts from a nonzero lineage.
        seed_calls = provider(app, project, statement_suffix=" SYNTHETIC PREVIOUS.")
        collect(app, project, request(app, project, max_calls=6))
        predecessor = app.unified.store.latest(project.id)
        assert predecessor["generation_progress"]["checkpoint_count"] == 5
        assert app.planning_jobs.store.get("offline-job")["status"] == "stopped"
        preview_calls = provider(app, project, statement_suffix=" SYNTHETIC CURRENT.")
        fake = app.settings.stream
        async def slow_fake(*a, **kw):
            await asyncio.sleep(max(0, args.delay))
            async for event in fake(*a, **kw):
                yield event
        app.settings.stream = slow_fake
        # No settings.save, stored provider config or fallback is used anywhere.
        app.operations["settings.get"] = lambda: {"adapters": [], "provider": {
            "adapter": "synthetic_local_only", "has_key": False,
            "config": {"model": "SYNTHETIC FAKE PROVIDER / ZERO REAL REQUESTS"}}}
        def forbidden(*a, **kw):
            raise RuntimeError("Synthetic preview has no real provider or credential configuration")
        app.operations["settings.save"] = forbidden
        app.operations["settings.test"] = forbidden
        result = {"label": LABEL, "frontend_verified": ready, "profile_is_new_temporary": True,
                  "real_provider_dispatches": 0, "credential_accesses": 0,
                  "synthetic_seed_calls": len(seed_calls), "retained_checkpoints": 5,
                  "new_fake_provider_calls": len(preview_calls), "gui_started": False,
                  "instructions": "选择此 SYNTHETIC 项目。输入下面文本，启用有界任务，明确设为 3 阶段 / 12 次 / 1179648 B 并确认后发送。",
                  "input": INPUT}
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if args.check:
            return
        import webview
        from evograph.transport.desktop import DesktopBridge, check_desktop_environment
        check_desktop_environment()
        os.environ.setdefault("QT_API", "pyside6")
        bridge = DesktopBridge(app)
        window = webview.create_window(LABEL, str(ROOT / "dist/index.html"), js_api=bridge,
                                       width=1480, height=960, min_size=(1050, 720))
        bridge._window = window
        webview.start(http_server=True, gui="qt")


if __name__ == "__main__":
    main()
