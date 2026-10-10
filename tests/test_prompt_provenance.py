"""Offline handoff provenance checks, without Application/provider/auth startup.

Run: PYTHONPATH=backend python -m unittest discover -s tests
-p test_prompt_provenance.py -v
"""

from types import SimpleNamespace
import unittest

from evograph.application.acceptance import AcceptanceService
from evograph.domain.models import Baseline, BehaviorRevision, Milestone, Project


def fixture():
    behavior = BehaviorRevision(id="B", behavior_key="behavior", version=1,
                                statement="Saved requirement", owner="M")
    milestone = Milestone(id="M", title="Saved milestone", intent="Saved intent",
                          lease_active=True, status="IN_PROGRESS", behavior_revision_ids=["B"])
    project = Project(name="Offline fixture", revision=4, milestones=[milestone],
                      behaviors=[behavior], baselines=[Baseline(
                          id="base", number=1, commit="saved-commit", fingerprint="saved-fingerprint",
                          file_count=1, complete=True,
                      )])
    return project, milestone


class PromptProvenanceTests(unittest.TestCase):
    def test_implementation_uses_its_read_snapshot_without_refresh_or_save(self):
        project, milestone = fixture()
        before = project.model_dump()
        db = SimpleNamespace(get=lambda project_id: project)
        service = AcceptanceService(db, SimpleNamespace())
        expected = service._implementation_prompt(project, milestone)
        result = service.implementation(project.id, milestone.id)
        self.assertEqual(result, {"prompt": expected, "project_revision": 4})
        self.assertEqual(project.model_dump(), before)
        project.revision = 9
        self.assertEqual(result["project_revision"], 4)

    def test_acceptance_returns_post_refresh_post_save_render_version(self):
        project, milestone = fixture()
        calls = []

        def refresh(project_id):
            self.assertEqual(project_id, project.id)
            calls.append("refresh")
            project.revision = 7
            return project

        def save(saved, kind, detail):
            self.assertIs(saved, project)
            self.assertEqual((kind, detail), ("acceptance_prepared", "M"))
            calls.append("save")
            saved.revision += 1
            return saved

        service = AcceptanceService(SimpleNamespace(save=save), SimpleNamespace(refresh=refresh))
        render = service._acceptance_prompt

        def record_render(saved, node, request, template):
            calls.append(("render", saved.revision))
            return render(saved, node, request, template)

        service._acceptance_prompt = record_render
        result = service.prepare(project.id, milestone.id)
        self.assertEqual(calls, ["refresh", "save", ("render", 8)])
        self.assertEqual(result["project_revision"], 8)
        self.assertEqual(result["request_id"], project.acceptance_requests[-1].id)
        self.assertIn(result["request_id"], result["prompt"])
        self.assertIn("Saved requirement", result["prompt"])
        project.revision = 10
        self.assertEqual(result["project_revision"], 8)


if __name__ == "__main__":
    unittest.main()
