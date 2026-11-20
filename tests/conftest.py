import pytest
import uuid
from pathlib import Path
from xhs_context.storage import Store
from xhs_context.processing import finish_content


def pytest_configure(config):
    if config.option.basetemp is None:
        root = Path(__file__).resolve().parents[1]
        target = (root / ".tmp" / ("pytest-" + uuid.uuid4().hex)).resolve()
        assert target.is_relative_to(root) and not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(target)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "library")
    value.initialize()
    return value


def note(number=1, body="马德里小吃推荐，土豆饼。", title="中文收藏"):
    identifier = f"{number:024x}"
    return finish_content({"note_id": identifier, "source_url": f"https://www.xiaohongshu.com/explore/{identifier}",
                          "canonical_url": f"https://www.xiaohongshu.com/explore/{identifier}",
                          "source_kind": "synthetic_fixture", "title": title, "body": body,
                          "body_state": "available", "expected_images": 0, "images": []})


def card(number):
    n = note(number)
    return {k: n[k] for k in ("note_id", "source_url", "title")}


def create(store, job_id, count=None):
    from xhs_context.jobs import create_job
    return create_job(store, {"job_id": job_id, "expected_count": count,
                             "collection": {"id": "collection", "name": "测试收藏",
                                            "source_url": "https://www.xiaohongshu.com/user/profile/test"}})

