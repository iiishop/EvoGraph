"""Native question/answer fixture: real app, fresh temporary profile, fake provider only.

Run --check for headless validation. Without --check, open the explicitly synthetic
project and click its ordinary answer option. No real profile/config is imported.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from preview_bounded_job import NoSecrets, guards

ROOT = Path(__file__).resolve().parents[1]
LABEL = "SYNTHETIC · 有界任务等待回答 · 假模型 / 零真实请求"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests")]
    with TemporaryDirectory(prefix="evograph-synthetic-bounded-question-") as directory:
        profile = Path(directory).resolve()
        guards(profile)
        from evograph.application.api import Application
        from evograph.domain.plan_contracts import planning_payload
        from evograph.frontend_build import bundle_status
        from test_agent_stream import tool_chunks
        from test_bounded_planning_job import collect, provider, request, seven

        ready, reason = bundle_status(ROOT, ROOT / "dist")
        if not ready:
            raise SystemExit(reason + "; build the unchanged frontend in this isolated worktree")
        app = Application(profile, NoSecrets())
        project = seven.__wrapped__(app)
        project.name = LABEL
        project.description = "纯本机假模型演示：任务两次假调用后等待回答；点击问题选项走普通回答路径。不会调用真实模型，不证明方案质量。"
        project = app.db.save(project, "synthetic_label")
        provider(app, project)
        baseline_provider = app.settings.stream
        seed_calls = []
        async def question_provider(messages, schemas, **kwargs):
            seed_calls.append([s["function"]["name"] for s in schemas])
            if len(seed_calls) == 2:
                async for event in tool_chunks("ask_user", {
                    "prompt": "SYNTHETIC：保留只读、本地 Markdown 检查范围吗？",
                    "category": "decision", "options": ["保留本地只读范围", "稍后决定"],
                }):
                    yield event
            else:
                async for event in baseline_provider(messages, schemas, **kwargs):
                    yield event
        app.settings.stream = question_provider
        seed_events = collect(app, project, request(app, project))
        original_job = deepcopy(app.planning_jobs.store.get("offline-job"))
        original_candidate = deepcopy(app.unified.store.latest(project.id))
        current = app.db.get(project.id)
        assert len(seed_calls) == 2 and current.question
        assert not [e for e in seed_events if e["type"] == "error"]
        assert seed_events[-1]["summary"]["status"] == "waiting"
        assert original_job["status"] == "stopped" and original_job["stop_reason"] == "question"
        assert not app.planning_jobs.view(original_job)["can_continue"]
        assert planning_payload(current) == planning_payload(project)

        def receipt(stage, events, answer_calls):
            saved = app.db.get(project.id)
            job = app.planning_jobs.store.get("offline-job")
            with app.db.connect() as connection:
                rows = connection.execute("SELECT detail FROM events WHERE project_id=? "
                    "AND kind='agent_turn_finished' ORDER BY rowid", (project.id,)).fetchall()
            result = {"stage": stage, "label": LABEL, "frontend_verified": True,
                "profile_is_new_temporary": True, "profile": str(profile), "project_id": project.id,
                "real_provider_dispatches": 0, "credential_accesses": 0,
                "seed_fake_dispatches": len(seed_calls), "answer_fake_dispatches": answer_calls,
                "question": saved.question.model_dump() if saved.question else None,
                "canonical_planning_unchanged": planning_payload(saved) == planning_payload(project),
                "canonical_revision": saved.revision,
                "original_job_unchanged": job == original_job,
                "original_candidate_unchanged": app.unified.store.get(original_candidate["id"]) == original_candidate,
                "original_job_hash": digest(original_job), "original_candidate_hash": digest(original_candidate),
                "job": app.planning_jobs.view(job),
                "charged_calls": original_candidate["metrics"]["calls"],
                "turn_receipts": [json.loads(row[0]) for row in rows],
                "stream_errors": [e for e in events if e["type"] == "error"],
                "final_status": events[-1].get("summary", {}).get("status"),
                "changed": events[-1].get("changed"),
                "gui_requested": not args.check, "question_preseeded_by_mock": True,
                "ordinary_answer_path": "headless check" if args.check else "native action required"}
            exported = {"project": saved.model_dump(), "original-job": job,
                "original-candidate": app.unified.store.get(original_candidate["id"]),
                "current-candidate": app.unified.store.latest(project.id),
                "terminal-events": [json.loads(row[0]) for row in rows],
                "fake-call-tally": {"seed": len(seed_calls), "answer": answer_calls,
                                    "real_provider_dispatches": 0}}
            for name, value in exported.items():
                (evidence / f"{stage}-{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            result["export_files"] = {f"{stage}-{name}.json": hashlib.sha256(
                (evidence / f"{stage}-{name}.json").read_bytes()).hexdigest() for name in exported}
            (evidence / f"{stage}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            return result

        receipt("waiting-question", seed_events, 0)
        answer_calls = []
        async def answer_provider(messages, schemas, **kwargs):
            candidate = app.unified.store.latest(project.id)
            assert not candidate.get("planning_job"), "The question must use ordinary answering"
            assert app.planning_jobs.store.get("offline-job") == original_job
            answer_calls.append(True)
            yield {"type": "text", "text": "SYNTHETIC：已记录回答。原有界任务保持关闭，未应用新方案。"}
            yield {"type": "usage", "tokens": 11}
        app.settings.stream = answer_provider
        normal_stream = app.agent.stream
        async def watched_stream(*a, **kw):
            events = []
            async for event in normal_stream(*a, **kw):
                events.append(event)
                if event["type"] == "done":
                    result = receipt("answered-question", events, len(answer_calls))
                    assert result["question"] is None
                    assert result["original_job_unchanged"] and result["original_candidate_unchanged"]
                    assert not result["stream_errors"] and result["final_status"] == "completed"
                    assert result["canonical_planning_unchanged"] and result["changed"] is False
                yield event
        app.agent.stream = watched_stream
        app.operations["settings.get"] = lambda: {"adapters": [], "provider": {
            "adapter": "synthetic_local_only", "has_key": False,
            "config": {"model": "SYNTHETIC FAKE PROVIDER / ZERO REAL REQUESTS"}}}
        def forbidden(*a, **kw):
            raise RuntimeError("Synthetic preview has no real provider or credential configuration")
        app.operations["settings.save"] = forbidden
        app.operations["settings.test"] = forbidden
        print(json.dumps({"label": LABEL, "profile": str(profile), "project_id": project.id,
            "evidence_dir": str(evidence), "real_provider_dispatches": 0, "frontend_verified": ready,
            "instructions": "选择此 SYNTHETIC 项目。先截图等待回答问题的状态，再点击问题中的“保留本地只读范围”，确认问题消失且原任务仍关闭、累计两次调用不变。"}, ensure_ascii=False, indent=2), flush=True)
        if args.check:
            async def answer():
                return [e async for e in app.agent.stream(project.id, "保留本地只读范围", question_id=current.question.id)]
            asyncio.run(answer())
            assert len(answer_calls) == 1
            print("CHECK PASSED: waiting question and ordinary answer, zero real provider calls", flush=True)
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
