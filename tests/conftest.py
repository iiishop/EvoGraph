import pytest
from evograph.application.api import Application
from evograph.domain.models import PlanProposal


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name, "")

    def set(self, name, secret):
        self.values[name] = secret


@pytest.fixture
def app(tmp_path):
    return Application(tmp_path / "data", MemorySecrets())


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text("def login(): return True\n")
    return repo


def proposal(target="Login V1", statement="Username login succeeds", node="M01", key="auth.login"):
    return PlanProposal.model_validate(
        {
            "target": target,
            "summary": "Small verified slice",
            "milestones": [
                {
                    "id": node,
                    "title": "Login",
                    "intent": "Introduce login",
                    "scope": ["auth.py"],
                    "behaviors": [{"key": key, "statement": statement}],
                    "resources": ["api:auth"],
                }
            ],
        }
    )


def apply_proposal(app, p, plan):
    p = app.db.get(p.id)
    p.proposal = plan
    p.proposal_revision = p.revision + 1
    p = app.db.save(p, "test_proposal")
    return app.planning.apply(p.id, p.revision)


@pytest.fixture
def planned(app, repository):
    p = app.projects.create("Test", repository=str(repository))
    p = apply_proposal(app, p, proposal())
    app.execution.refresh(p.id)
    app.execution.resolve_obligation(
        p.id, "M01", "scope", True, "Reviewed auth.py and the executable acceptance script"
    )
    return app.db.get(p.id)
