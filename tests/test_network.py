import httpx
import pytest

from xhs_context.common import XhsError
from xhs_context.network import AnonymousFetcher, proxy_for


@pytest.fixture
def proxied(monkeypatch):
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"https": "http://127.0.0.1:7897"})
    monkeypatch.setattr("urllib.request.proxy_bypass", lambda host: False)
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: pytest.fail("Proxy resolves the source DNS"))


def client_factory(monkeypatch, handler, proxies):
    actual = httpx.Client
    def client(**kwargs):
        proxies.append(kwargs.pop("proxy"))
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert kwargs.get("verify", True) is True
        return actual(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "Client", client)


def test_configured_proxy_keeps_redirects_anonymous(proxied, monkeypatch):
    seen, proxies = [], []
    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "/explore/test", "set-cookie": "session=should-not-travel"})
        return httpx.Response(200, content=b"note")
    client_factory(monkeypatch, handler, proxies)
    assert AnonymousFetcher().get("https://www.xiaohongshu.com/user/profile/test")[0] == b"note"
    assert proxies == ["http://127.0.0.1:7897"] * 2
    assert all("cookie" not in request.headers for request in seen)


@pytest.mark.parametrize("target,code", [("https://evil.example/", "URL_NOT_ALLOWED"),
                                        ("/login?redirect=x", "ACCESS_BLOCKED"),
                                        ("/website-login/error?error_code=300013", "ACCESS_RESTRICTED"),
                                        ("/website-login/error?error_code=300017", "SOURCE_LINK_EXPIRED"),
                                        ("/website-login/error?error_code=unknown", "ACCESS_BLOCKED")])
def test_proxy_redirects_still_enforce_host_and_login_boundaries(proxied, monkeypatch, target, code):
    proxies = []
    client_factory(monkeypatch, lambda request: httpx.Response(302, headers={"location": target}), proxies)
    with pytest.raises(XhsError) as error:
        AnonymousFetcher().get("https://www.xiaohongshu.com/explore/test")
    assert error.value.code == code and len(proxies) == 1


def test_proxy_bypass_keeps_direct_dns_validation(proxied, monkeypatch):
    monkeypatch.setattr("urllib.request.proxy_bypass", lambda host: True)
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("198.18.0.140", 443))])
    assert proxy_for("www.xiaohongshu.com") is None
    with pytest.raises(XhsError) as error:
        AnonymousFetcher().get("https://www.xiaohongshu.com/explore/test")
    assert error.value.code == "LOCAL_NETWORK_BLOCKED"
