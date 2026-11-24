from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import parse_qs, urljoin, urlsplit

from .common import XhsError

PAGE_HOSTS = {"www.xiaohongshu.com", "xiaohongshu.com", "m.xiaohongshu.com"}
NOTE_PATH = re.compile(r"/(?:explore|discovery/item|user/profile/[0-9a-fA-F]{24})/([0-9a-fA-F]{24})/?")


def validate_url(url, *, media=False):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        allowed = host in PAGE_HOSTS or (media and (host.endswith(".xhscdn.com") or host == "xhscdn.com" or
                                                    host == "ci.xiaohongshu.com"))
        if parsed.scheme != "https" or not allowed or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError()
        if len(url) > 10000 or any(ord(c) < 32 for c in url):
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise XhsError("URL_NOT_ALLOWED", "Only HTTPS Xiaohongshu page/CDN targets are accepted", exit_code=2) from None


def validate_page_url(url):
    return validate_url(url)


def validate_note_url(url, note_id):
    parsed = validate_url(url)
    match = NOTE_PATH.fullmatch(parsed.path)
    if not match or match.group(1).lower() != note_id.lower():
        raise XhsError("URL_NOTE_MISMATCH", "The actual note URL must match its note_id", exit_code=2)
    return parsed


def validate_note_response(url, note_id):
    """A response from another page cannot establish this note's disappearance."""
    try:
        return validate_note_url(url, note_id)
    except XhsError as exc:
        if exc.code != "URL_NOTE_MISMATCH":
            raise
        raise XhsError("NOTE_NOT_FOUND", "The response no longer belongs to the requested note", exit_code=5) from None


def public_addresses(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise XhsError("NETWORK_ERROR", "Unable to resolve the source host", exit_code=5) from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise XhsError("LOCAL_NETWORK_BLOCKED", "Local DNS returned a non-public address; check the configured proxy", exit_code=5)


def proxy_for(host):
    """Use the user's existing proxy settings, including Windows Internet Settings.

    An HTTP CONNECT proxy resolves the source host itself. Local fake-IP DNS
    answers must not be used to decide whether that proxied route is reachable.
    URL allowlisting and TLS certificate verification still apply on every hop.
    """
    from urllib.request import getproxies, proxy_bypass

    if proxy_bypass(host):
        return None
    proxies = getproxies()
    value = proxies.get("https") or proxies.get("all")
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.port == 0:
            raise ValueError()
    except ValueError:
        raise XhsError("PROXY_CONFIGURATION", "An HTTP or HTTPS proxy is required", exit_code=5) from None
    return value


class AnonymousFetcher:
    def get(self, url, *, media=False):
        import httpx
        limit = 25 * 1024 * 1024 if media else 5 * 1024 * 1024
        initial = validate_url(url, media=media)
        requested = NOTE_PATH.fullmatch(initial.path) if not media else None
        for redirect in range(4):
            parsed = validate_url(url, media=media)
            if parsed.path.rstrip("/") == "/website-login/error":
                code = parse_qs(parsed.query).get("error_code", [""])[0]
                reason = {"300013": "ACCESS_RESTRICTED", "300017": "SOURCE_LINK_EXPIRED"}.get(code, "ACCESS_BLOCKED")
                raise XhsError(reason, "The source redirected to an access error page", exit_code=5)
            if parsed.path.rstrip("/") in {"/login", "/signin"} or parsed.path.startswith("/website-login/"):
                raise XhsError("ACCESS_BLOCKED", "The source redirected to a login page", exit_code=5)
            if requested:
                validate_note_response(url, requested[1])
            proxy = proxy_for(parsed.hostname)
            if proxy is None:
                public_addresses(parsed.hostname)
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False, proxy=proxy,
                          headers={"User-Agent": "xhs-context/0.3 (personal local archive)",
                                   "Accept": "image/*" if media else "text/html"}) as client:
                client.cookies.clear()
                try:
                    with client.stream("GET", url) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if redirect == 3 or not response.headers.get("location"):
                                raise XhsError("REDIRECT_LIMIT", "Source redirect limit exceeded", exit_code=5)
                            url = urljoin(url, response.headers["location"])
                            continue
                        if response.status_code == 429:
                            raise XhsError("RATE_LIMITED", "Source rate limited this sync; resume explicitly later", exit_code=5)
                        if response.status_code in {401, 403}:
                            raise XhsError("ACCESS_BLOCKED", "Anonymous access was refused", exit_code=5)
                        if response.status_code in {404, 410}:
                            raise XhsError("SOURCE_UNAVAILABLE", "Source is unavailable", exit_code=5)
                        if response.status_code >= 400:
                            raise XhsError("HTTP_ERROR", f"Source returned HTTP {response.status_code}", exit_code=5)
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > limit:
                                raise XhsError("SOURCE_TOO_LARGE", "Source exceeds the configured size limit", exit_code=5)
                            chunks.append(chunk)
                        return b"".join(chunks), response.headers.get("content-type", ""), url
                except httpx.ProxyError:
                    raise XhsError("PROXY_ERROR", "The configured proxy could not connect", exit_code=5) from None
                except httpx.HTTPError:
                    # Transport messages can contain signed URLs. Return a bounded diagnosis.
                    raise XhsError("NETWORK_ERROR", "Anonymous source request failed or timed out", exit_code=5) from None
        raise XhsError("REDIRECT_LIMIT", "Source redirect limit exceeded", exit_code=5)

