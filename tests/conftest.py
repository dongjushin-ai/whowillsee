import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    import make_samples

    out = tmp_path_factory.mktemp("samples")
    make_samples.make_all(out)
    return out


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """각 테스트가 별도 data 폴더를 쓰도록 한다."""
    from app import storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                "WHOWILLSEE_PROVIDER", "WHOWILLSEE_MODEL", "WHOWILLSEE_DEMO", "WHOWILLSEE_LOCK_SETTINGS"):
        monkeypatch.delenv(var, raising=False)
    yield
