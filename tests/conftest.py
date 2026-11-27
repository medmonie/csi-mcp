import pytest


@pytest.fixture(autouse=True)
def isolate_client_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_MCP_OUTPUT", str(tmp_path / "output"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
