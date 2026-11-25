import json
import subprocess
import sys
import threading

import httpx
import pytest

from conftest import card, note
from xhs_context.common import XhsError
from xhs_context.network import validate_note_url, validate_url, public_addresses
from xhs_context.server import make_server


def test_real_loopback_receiver_commits_and_rejects_wrong_origin(store):
    token = "test-token-only"
    server = make_server(store, token, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(base_url=url, trust_env=False) as client:
            assert client.get("/v1/status").status_code == 401
            auth = {"Authorization": "Bearer " + token}
            assert client.get("/v1/status", headers={**auth, "Origin": "https://evil.example"}).status_code == 403
            assert client.get('/v1/invalid-notes').status_code == 401
            assert client.get('/v1/invalid-notes', headers={**auth, 'Origin': 'https://evil.example'}).status_code == 403
            invalid = client.get('/v1/invalid-notes?limit=10&offset=0', headers=auth)
            assert invalid.status_code == 200 and invalid.json()['data'] == {'total': 0, 'items': [], 'has_more': False}
            assert client.get('/v1/invalid-notes?limit=0', headers=auth).status_code == 400
            create = {"job_id": "http-job", "expected_count": 1, "collection": {
                "id": "http-folder", "name": "HTTP test", "source_url": "https://www.xiaohongshu.com/user/profile/test"}}
            assert client.post("/v1/jobs", json=create, headers=auth).status_code == 200
            batch = {"seq": 0, "items": [card(1)]}
            first = client.post("/v1/jobs/http-job/batches", json=batch, headers=auth).json()
            second = client.post("/v1/jobs/http-job/batches", json=batch, headers=auth).json()
            assert first["data"]["duplicate"] is False and second["data"]["duplicate"] is True
            client.post("/v1/jobs/http-job/finish", json={"batch_count": 1, "end_evidence": "page_end"}, headers=auth).raise_for_status()
            result = client.get("/v1/status?job_id=http-job", headers=auth).json()
            assert result["data"]["job"]["list_complete"]
            assert not result["data"]["job"]["content_complete"]
            with store.read() as db:
                assert db.execute("SELECT count(*) FROM job_items").fetchone()[0] == 1
            for number in (2, 3):
                store.publish(note(number))
            image_job = client.post('/v1/image-jobs', json={
                'note_ids': [note(number)['note_id'] for number in (2, 3)]}, headers=auth).json()['data']
            result_url = '/v1/image-jobs/' + image_job['job_id']
            assert client.get(result_url).status_code == 401
            first = client.get(result_url, params={'limit': 1}, headers=auth)
            assert first.status_code == 200
            first = first.json()['data']
            second = client.get(result_url, params={'cursor': first['next_cursor']}, headers=auth).json()['data']
            assert first['notes'] == second['notes'] == 2
            assert first['results'][0]['note_id'] != second['results'][0]['note_id']
            assert second['next_cursor'] is None and second['results'][0]['revision_id']
            assert client.get(result_url, params={'limit': 0}, headers=auth).status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("url", [
    "http://www.xiaohongshu.com/explore/000000000000000000000001",
    "https://www.xiaohongshu.com.evil.example/",
    "https://evil.example@www.xiaohongshu.com/",
    "https://www.xiaohongshu.com:9999/",
    "file:///etc/passwd"])
def test_network_target_validation(url):
    with pytest.raises(XhsError):
        validate_url(url)


def test_dns_blocks_private_targets(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(XhsError):
        public_addresses("www.xiaohongshu.com")


@pytest.mark.parametrize("prefix", ["explore", "discovery/item", "user/profile/" + "1" * 24])
def test_note_routes_accept_actual_card_urls_and_reject_mismatched_ids(prefix):
    note_id = "2" * 24
    url = "https://www.xiaohongshu.com/" + prefix + "/" + note_id + "?xsec_token=fixture-only"
    parsed = validate_note_url(url, note_id)
    assert parsed.query == "xsec_token=fixture-only"
    with pytest.raises(XhsError):
        validate_note_url(url, "3" * 24)


def test_cli_stdout_json_and_error_exit(tmp_path):
    root = tmp_path / "中文 资料"
    def command(*args):
        return subprocess.run([sys.executable, "-m", "xhs_context", "--library", str(root), *args],
                              capture_output=True, text=True, encoding="utf-8", cwd=tmp_path)
    assert command("init").returncode == 0
    result = command("search", "--query", "并不存在")
    assert result.returncode == 0
    assert json.loads(result.stdout)["data"]["items"] == []
    bad = command("search", "--limit", "0", "--query", "x")
    assert bad.returncode == 2
    assert json.loads(bad.stdout)["error"]["code"] == "INVALID_ARGUMENT"
    assert "Traceback" not in bad.stderr
    stdin = subprocess.run([sys.executable, "-m", "xhs_context", "--library", str(root),
                            "search", "--input", "-"], input='{"queries":["中文短词"]}',
                           capture_output=True, text=True, encoding="utf-8", cwd=tmp_path)
    assert stdin.returncode == 0
    assert json.loads(stdin.stdout)["data"]["effective_queries"] == ["中文短词"]

