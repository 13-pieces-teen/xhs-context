"""Content-addressed writes repair corruption without replacing valid objects."""
import pytest

from xhs_context.common import digest


@pytest.mark.parametrize('damaged', [b'', b'bad', b'wrong payload bytes'])
def test_put_object_atomically_repairs_corrupted_existing_file(store, monkeypatch, damaged):
    data = b'correct payload bytes'
    first = store.put_object(data)
    path = store.object_path(first['object_path'])
    path.write_bytes(damaged)
    import xhs_context.storage as storage
    replace = storage.os.replace
    replacements = []

    def checked_replace(source, target):
        assert target == path and path.read_bytes() == damaged
        assert source.read_bytes() == data and digest(source.read_bytes()) == first['sha256']
        replacements.append((source, target))
        replace(source, target)

    monkeypatch.setattr(storage.os, 'replace', checked_replace)
    assert store.put_object(data) == first
    assert path.read_bytes() == data and len(replacements) == 1


def test_put_object_preserves_correct_existing_file(store, monkeypatch):
    data = b'correct payload bytes'
    first = store.put_object(data)
    monkeypatch.setattr('xhs_context.storage.os.replace', lambda *args: pytest.fail('Intact object must not be replaced'))
    assert store.put_object(data) == first
    assert store.object_path(first['object_path']).read_bytes() == data
