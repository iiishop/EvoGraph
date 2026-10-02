from pathlib import Path

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.repository import ReadFile, read_repository_file
from evograph.infrastructure.repository import readable


@pytest.mark.parametrize(
    "name",
    [
        ".npmrc",
        ".PyPirc",
        ".NETRC",
        "_netrc",
        ".git-credentials",
        "credentials.json",
        ".AWS/CREDENTIALS",
        ".ssh/config",
        ".AZURE/profile.json",
        ".kube/config",
        ".config/gcloud/application_default_credentials.json",
        "nested/private.KEY",
    ],
)
def test_auth_paths_are_absent_from_catalog_and_denied_by_read_tool(app, planned, repository, name):
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SYNTHETIC_AUTH_TEST_DATA_ONLY")
    candidates = app.references.catalog(planned.id)["items"]
    assert all(item["id"] != "repo:" + name for item in candidates)
    with pytest.raises(ValueError, match="允许的源码"):
        read_repository_file(ToolContext(planned.id, app), ReadFile(path=name))


@pytest.mark.parametrize(
    "name",
    [
        r"C:\project\.AWS\credentials",
        r"folder\.SSH\ID_RSA",
        r"x\.CONFIG\GCLOUD\token.json",
        r"x\.NPMRC ",
        r"x\secret.PEM",
        "./x/../.netrc",
        ".npmrc.",
    ],
)
def test_portable_auth_path_normalization(name):
    assert not readable(Path(name))


def test_safe_source_stays_readable_but_symlink_to_auth_is_rejected(app, planned, repository):
    assert readable(repository / "auth.py")
    assert readable(repository / "docs" / "credentials-guide.md")
    private = repository / ".npmrc"
    private.write_text("SYNTHETIC_AUTH_TEST_DATA_ONLY")
    (repository / "public.py").symlink_to(private)
    assert all(
        item["id"] != "repo:public.py" for item in app.references.catalog(planned.id)["items"]
    )
    with pytest.raises(ValueError):
        read_repository_file(ToolContext(planned.id, app), ReadFile(path="public.py"))
