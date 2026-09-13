#!/usr/bin/env python3
"""Snapshot the public site into a static tree."""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from html.parser import HTMLParser
from pathlib import Path

ORIGIN = "https://erccommunities.com"
HOSTS = {"erccommunities.com", "www.erccommunities.com"}
ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "snapshot"
ARCHIVE = ROOT / "archive"
WP_JSON_DIR = ARCHIVE / "wp-json"
USER_AGENT = "ERC-Communities-Static-Snapshot/1.0 (+https://github.com/TheDevWhoSaysNi/erccommunities.com)"
SKIP_PREFIXES = (
    "/wp-admin",
    "/wp-login.php",
    "/xmlrpc.php",
    "/cdn-cgi/",
    "/wp-json",
    "/feed",
    "/comments/feed",
)
SKIP_QUERY_KEYS = {"replytocom", "unapproved", "moderation-hash"}
TEXT_SUFFIXES = {
    ".html",
    ".htm",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".txt",
    ".svg",
    ".map",
}
SEED_PATHS = [
    "/",
    "/home/",
    "/about-us/",
    "/our-models/",
    "/leadership/",
    "/profitability/",
    "/contact-us/",
    "/offering/",
    "/schedule-a-call/",
    "/schedule-a-call-thank-you/",
    "/privacy-policy-2/",
    "/accredited/",
    "/robots.txt",
    "/favicon.ico",
    "/wp-content/themes/hello-theme-child-master/style.css",
]

URL_RE = re.compile(
    r"""(?P<url>(?:https?:)?//(?:www\.)?erccommunities\.com[^"'()\s<>\\]*|/wp-(?:content|includes)[^"'()\s<>\\]*)""",
    re.IGNORECASE,
)
CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.IGNORECASE)
SRCSET_RE = re.compile(r"""(\S+)(?:\s+\d+[wx])?""")
CFEMAIL_RE = re.compile(
    r"""<a[^>]*class="[^"]*__cf_email__[^"]*"[^>]*data-cfemail="([0-9a-f]+)"[^>]*>.*?</a>""",
    re.IGNORECASE | re.DOTALL,
)
CFEMAIL_SPAN_RE = re.compile(
    r"""<span[^>]*class="[^"]*__cf_email__[^"]*"[^>]*data-cfemail="([0-9a-f]+)"[^>]*>.*?</span>""",
    re.IGNORECASE | re.DOTALL,
)


class AssetParser(HTMLParser):
    ATTRS = {
        "href",
        "src",
        "srcset",
        "data-src",
        "data-lazy-src",
        "data-lazy-srcset",
        "data-bg",
        "data-background",
        "data-settings",
        "content",
        "action",
        "poster",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value for name, value in attrs if value}
        for name, value in attrs:
            if not value:
                continue
            lname = name.lower()
            if lname == "content":
                prop = (attr_map.get("property") or attr_map.get("name") or "").lower()
                if not any(key in prop for key in ("image", "url", "logo")):
                    continue
            if lname in {"srcset", "data-srcset", "data-lazy-srcset", "imagesrcset"}:
                for part in value.split(","):
                    url = part.strip().split(" ")[0]
                    if url:
                        self.found.append(url)
            elif lname in self.ATTRS or lname.endswith("src") or lname.endswith("href"):
                if value.startswith("{") or value.startswith("["):
                    continue
                self.found.append(value)


def decode_cfemail(hexstr: str) -> str:
    data = bytes.fromhex(hexstr)
    key = data[0]
    return "".join(chr(b ^ key) for b in data[1:])


def unescape_url(url: str) -> str:
    return (
        url.replace("&amp;", "&")
        .replace("\\/", "/")
        .replace("&quot;", "")
        .rstrip("\\")
        .strip(" \t\n\r\"'")
    )


def normalize_url(url: str, base: str) -> str | None:
    url = unescape_url(url)
    if not url or url.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return None
    if any(ch in url for ch in (" ", "{", "}", "*", "+", "<", ">", '"', "'")):
        return None
    if url.startswith("//"):
        url = "https:" + url
    joined = urllib.parse.urljoin(base, url)
    parsed = urllib.parse.urlparse(joined)
    host = parsed.hostname or ""
    if host.lower() not in HOSTS:
        return None
    path = parsed.path or "/"
    if path.lower().endswith(".php"):
        return None
    if any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
        return None
    if any(ch in path for ch in (" ", "{", "}", "*", "$")):
        return None
    if path.startswith("/{") or "{" in path:
        return None
    # Meta/JSON false positives and speculation-rules globs.
    if path.endswith("/*") or "/{" in path:
        return None
    last = path.rsplit("/", 1)[-1]
    if last.startswith("{") or last.startswith("$"):
        return None
    suffix = Path(path).suffix.lower()
    asset_path = path.startswith("/wp-content/") or path.startswith("/wp-includes/")
    if not asset_path and not suffix:
        slug = last or path.strip("/")
        if len(slug) < 3 or not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
            if path not in {"/", ""}:
                return None
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for key in SKIP_QUERY_KEYS:
        query.pop(key, None)
    # Drop cache-busting query strings for files; keep none for HTML paths.
    clean = parsed._replace(scheme="https", netloc="erccommunities.com", query="", fragment="")
    return urllib.parse.urlunparse(clean)


def local_path_for(url: str, content_type: str | None, is_html: bool) -> Path:
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path)
    if not path or path == "/":
        return SNAPSHOT / "index.html"
    if path.endswith("/"):
        return SNAPSHOT / path.lstrip("/") / "index.html"
    suffix = Path(path).suffix.lower()
    if is_html or (not suffix and content_type and "html" in content_type):
        return SNAPSHOT / path.lstrip("/") / "index.html"
    return SNAPSHOT / path.lstrip("/")


def is_probably_text(path: Path, content_type: str | None, body: bytes) -> bool:
    if content_type:
        if any(x in content_type for x in ("text/", "javascript", "json", "xml", "svg")):
            return True
        if "octet-stream" not in content_type and not content_type.startswith(("image/", "font/", "audio/", "video/", "application/pdf")):
            if path.suffix.lower() in TEXT_SUFFIXES:
                return True
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return body.startswith((b"<!DOCTYPE", b"<!doctype", b"<html", b"/*", b"{"))


def rewrite_origin_urls(text: str) -> str:
    replacements = (
        ("https://www.erccommunities.com/", "/"),
        ("http://www.erccommunities.com/", "/"),
        ("https://erccommunities.com/", "/"),
        ("http://erccommunities.com/", "/"),
        ("https://www.erccommunities.com", "/"),
        ("http://www.erccommunities.com", "/"),
        ("https://erccommunities.com", "/"),
        ("http://erccommunities.com", "/"),
        ("//www.erccommunities.com/", "/"),
        ("//erccommunities.com/", "/"),
        ("//www.erccommunities.com", "/"),
        ("//erccommunities.com", "/"),
        ("https:\\/\\/www.erccommunities.com\\/", "/"),
        ("https:\\/\\/erccommunities.com\\/", "/"),
        ("https:\\/\\/www.erccommunities.com", "/"),
        ("https:\\/\\/erccommunities.com", "/"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = CFEMAIL_RE.sub(
        lambda m: f'<a href="mailto:{decode_cfemail(m.group(1))}">{decode_cfemail(m.group(1))}</a>',
        text,
    )
    text = CFEMAIL_SPAN_RE.sub(lambda m: decode_cfemail(m.group(1)), text)
    text = re.sub(
        r"<script>\(function\(\)\{function c\(\)\{.*?challenge-platform.*?</script>",
        "",
        text,
        flags=re.S,
    )
    text = re.sub(
        r'<script data-cfasync="false" src="/cdn-cgi/scripts/[^"]+"></script>',
        "",
        text,
    )
    return text


def extract_urls(body: str, base: str, source_path: str | None = None) -> set[str]:
    found: set[str] = set()
    suffix = Path(source_path or "").suffix.lower()
    parse_html = suffix in {"", ".html", ".htm", ".xml", ".svg"} or "<html" in body[:400].lower()
    parse_css = suffix == ".css" or "url(" in body[:2000]
    if parse_html:
        parser = AssetParser()
        try:
            parser.feed(body)
            parser.close()
        except Exception:
            pass
        for raw in parser.found:
            normalized = normalize_url(raw, base)
            if normalized:
                found.add(normalized)
    if parse_html or suffix in {".css", ".html", ".htm", ".svg"}:
        for match in CSS_URL_RE.finditer(body):
            normalized = normalize_url(match.group(1), base)
            if normalized:
                found.add(normalized)
    if suffix != ".js":
        for match in URL_RE.finditer(body):
            normalized = normalize_url(match.group("url"), base)
            if normalized:
                found.add(normalized)
    return found


def fetch(url: str, opener: urllib.request.OpenerDirector, attempts: int = 4) -> tuple[int, str | None, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with opener.open(req, timeout=90) as resp:
                final = resp.geturl()
                content_type = resp.headers.get("Content-Type")
                return resp.status, content_type, resp.read(), final
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Content-Type") if err.headers else None, err.read() or b"", url
        except (TimeoutError, urllib.error.URLError, ssl.SSLError, ConnectionError) as err:
            last_error = err
            time.sleep(min(8, attempt * 2))
    raise last_error or TimeoutError(url)


def wp_paginate(opener: urllib.request.OpenerDirector, endpoint: str) -> list[object]:
    items: list[object] = []
    page = 1
    while True:
        url = f"{ORIGIN}{endpoint}{'&' if '?' in endpoint else '?'}per_page=100&page={page}"
        status, content_type, body, _ = fetch(url, opener)
        if status != 200:
            break
        data = json.loads(body.decode("utf-8"))
        if not data:
            break
        if isinstance(data, list):
            items.extend(data)
            if len(data) < 100:
                break
        else:
            items.append(data)
            break
        page += 1
        time.sleep(0.15)
    return items


def dump_wp_json(opener: urllib.request.OpenerDirector) -> list[str]:
    WP_JSON_DIR.mkdir(parents=True, exist_ok=True)
    page_urls: list[str] = []
    collections = {
        "pages": "/wp-json/wp/v2/pages",
        "posts": "/wp-json/wp/v2/posts",
        "media": "/wp-json/wp/v2/media",
        "users": "/wp-json/wp/v2/users",
        "types": "/wp-json/wp/v2/types",
        "statuses": "/wp-json/wp/v2/statuses",
        "categories": "/wp-json/wp/v2/categories",
        "tags": "/wp-json/wp/v2/tags",
    }
    for name, endpoint in collections.items():
        data = wp_paginate(opener, endpoint)
        (WP_JSON_DIR / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"  wp-json {name}: {len(data) if isinstance(data, list) else 1}")
        if name == "pages":
            for item in data:
                link = item.get("link")
                if link:
                    page_urls.append(link)
        if name == "media":
            for item in data:
                source = item.get("source_url")
                if source:
                    page_urls.append(source)
                details = (item.get("media_details") or {}).get("sizes") or {}
                for size in details.values():
                    if size.get("source_url"):
                        page_urls.append(size["source_url"])
    index_status, _, index_body, _ = fetch(f"{ORIGIN}/wp-json/", opener)
    if index_status == 200:
        parsed = json.loads(index_body.decode("utf-8"))
        (WP_JSON_DIR / "index.json").write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    return page_urls


def write_redirects(redirects: list[tuple[str, str, int]]) -> None:
    lines = ["# Frozen WordPress snapshot redirects", ""]
    seen: set[str] = set()
    for source, target, code in redirects:
        src_path = urllib.parse.urlparse(source).path or "/"
        dst_path = urllib.parse.urlparse(target).path or "/"
        if src_path == dst_path or src_path in seen:
            continue
        seen.add(src_path)
        lines.append(f"{src_path} {dst_path} {code}")
    if len(lines) > 2:
        (SNAPSHOT / "_redirects").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_headers() -> None:
    (SNAPSHOT / "_headers").write_text(
        """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin

/wp-content/*
  Cache-Control: public, max-age=31536000, immutable

/wp-includes/*
  Cache-Control: public, max-age=31536000, immutable
""",
        encoding="utf-8",
    )


def main() -> None:
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    print("Dumping public WordPress REST API…")
    extra_urls = dump_wp_json(opener)

    queue: deque[str] = deque()
    queued: set[str] = set()

    def enqueue(url: str) -> None:
        normalized = normalize_url(url, ORIGIN)
        if not normalized or normalized in queued:
            return
        queued.add(normalized)
        queue.append(normalized)

    for path in SEED_PATHS:
        enqueue(ORIGIN + path)
    for url in extra_urls:
        enqueue(url)

    downloaded: dict[str, dict[str, object]] = {}
    redirects: list[tuple[str, str, int]] = []
    failures: list[dict[str, object]] = []

    print("Crawling public HTML and assets…")
    while queue:
        url = queue.popleft()
        existing_file = None
        for candidate in (
            local_path_for(url, None, False),
            local_path_for(url, "text/html", True),
        ):
            if candidate.exists():
                existing_file = candidate
                break
        if existing_file is not None:
            if existing_file.suffix.lower() in TEXT_SUFFIXES or existing_file.name == "index.html":
                try:
                    text = existing_file.read_text(encoding="utf-8", errors="replace")
                    for found in extract_urls(text, url, str(existing_file)):
                        enqueue(found)
                except OSError:
                    pass
            continue
        try:
            status, content_type, body, final = fetch(url, opener)
        except Exception as err:
            failures.append({"url": url, "status": 0, "error": str(err)})
            print(f"  ERROR {url} ({err})")
            continue
        time.sleep(0.05)
        if status in {301, 302, 303, 307, 308} or (final and normalize_url(final, ORIGIN) != url):
            dest = normalize_url(final, ORIGIN) or final
            redirects.append((url, dest, 301 if status == 200 else status))
            if dest.startswith("https://erccommunities.com"):
                enqueue(dest)
        if status >= 400:
            failures.append({"url": url, "status": status})
            print(f"  FAIL {status} {url}")
            continue
        html = bool(content_type and "html" in content_type)
        dest = local_path_for(url, content_type, html)
        dest.parent.mkdir(parents=True, exist_ok=True)
        text_file = html or is_probably_text(dest, content_type, body)
        if text_file:
            text = body.decode("utf-8", errors="replace")
            for found in extract_urls(text, url, str(dest)):
                enqueue(found)
            if html or dest.suffix.lower() in {".html", ".htm", ".css", ".js", ".svg", ".xml"}:
                text = rewrite_origin_urls(text)
            dest.write_text(text, encoding="utf-8")
            size = dest.stat().st_size
        else:
            dest.write_bytes(body)
            size = dest.stat().st_size
        downloaded[url] = {
            "path": str(dest.relative_to(ROOT)).replace("\\", "/"),
            "status": status,
            "content_type": content_type,
            "bytes": size,
        }
        print(f"  {status} {url} -> {dest.relative_to(ROOT)}")

    write_redirects(redirects)
    write_headers()
    manifest = {
        "origin": ORIGIN,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": downloaded,
        "redirects": redirects,
        "failures": failures,
    }
    (ARCHIVE / "snapshot-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Done. {len(downloaded)} files, {len(failures)} failures.")


if __name__ == "__main__":
    main()
