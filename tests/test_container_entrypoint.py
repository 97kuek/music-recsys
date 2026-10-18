"""Container audience is explicit; researcher startup does not execute a study plan."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.interfaces.http import container


@pytest.mark.parametrize("audience", ["participant", "researcher"])
def test_container_selects_audience_and_shared_database(monkeypatch, audience):
    verify, create, run = Mock(), Mock(), Mock()
    monkeypatch.setattr(container, "verify", verify)
    monkeypatch.setattr(container, "create_app", create)
    monkeypatch.setattr(container.uvicorn, "run", run)
    monkeypatch.setattr(container.os, "umask", Mock())
    monkeypatch.setattr(container, "setting", lambda name: "")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr("sys.argv", ["container", "--audience", audience])
    container.main()
    verify.assert_called_once_with(Path("/bundle"))
    kwargs = create.call_args.kwargs
    assert create.call_args.args == (Path("/bundle/config.json"), Path("/state/sessions.sqlite"))
    assert kwargs["audience"] == audience
    assert kwargs["study_path"] == (
        Path("/bundle/study.json") if audience == "participant" else None
    )
    assert run.call_args.kwargs == dict(
        host="0.0.0.0", port=8765, access_log=False, proxy_headers=False
    )
