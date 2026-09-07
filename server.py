#!/usr/bin/env python3
"""Small dashboard server for the APK intent patcher.

It intentionally uses only the Python standard library. Uploaded APKs and
generated artifacts stay inside the workspace. Builds are serialized because
the Android tool bootstrap cache is shared between jobs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email import policy
from email.parser import BytesParser
from html import unescape
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
JOBS_ROOT = ROOT / ".dashboard" / "jobs"
BUILD_SCRIPT = ROOT / "build.sh"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))
MAX_APK_BYTES = 650 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_APK_BYTES + 2 * 1024 * 1024
ALLOWED_LAUNCH_MODES = {"standard", "singleTop", "singleTask", "singleInstance"}
ALLOWED_SIGNING_MODES = {"debug", "test"}
JOB_ID_RE = re.compile(r"^[a-f0-9-]{36}$")
UPTODOWN_SEARCH_URLS = (
    os.environ.get("UPTODOWN_SEARCH_URL", "https://en.uptodown.com/android/search?query={query}"),
    "https://www.uptodown.com/android/search?query={query}",
)
UPTODOWN_HOSTS = {"uptodown.com", "www.uptodown.com", "en.uptodown.com"}
UPTODOWN_HOST_SUFFIX = ".uptodown.com"
BUILD_LOCK = threading.Semaphore(1)
JOBS: dict[str, "BuildJob"] = {}
JOBS_LOCK = threading.RLock()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def safe_name(name: str) -> str:
    cleaned = Path(name or "patched-app.apk").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned).strip(".-")
    if not cleaned.lower().endswith(".apk"):
        cleaned += ".apk"
    return cleaned or "patched-app.apk"


def is_uptodown_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme in {"http", "https"}
        and (hostname in UPTODOWN_HOSTS or hostname.endswith(UPTODOWN_HOST_SUFFIX))
    )


def normalize_uptodown_url(url: str, base_url: str = "https://en.uptodown.com/android") -> str | None:
    candidate = urllib.parse.urljoin(base_url, unescape(url.strip()))
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme == "http":
        candidate = urllib.parse.urlunparse(parsed._replace(scheme="https"))
    return candidate if is_uptodown_url(candidate) else None


class UptodownHTMLParser(HTMLParser):
    """Collect the small, stable subset of metadata used by the store adapter."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: dict[str, str] = {}
        self.links: list[dict[str, str]] = []
        self.images: list[str] = []
        self._anchor: dict[str, str] | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = attributes.get("property") or attributes.get("name")
            value = attributes.get("content")
            if key and value:
                self.metas[key.lower()] = value.strip()
        elif tag.lower() == "img":
            source = attributes.get("src") or attributes.get("data-src")
            if source:
                self.images.append(source.strip())
        elif tag.lower() == "a" and attributes.get("href"):
            self._anchor = {"href": attributes["href"], "title": attributes.get("title", "")}
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._anchor is not None:
            self._anchor["text"] = " ".join(" ".join(self._anchor_text).split())
            self.links.append(self._anchor)
            self._anchor = None
            self._anchor_text = []


def fetch_uptodown_page(url: str) -> tuple[str, str]:
    if not is_uptodown_url(url):
        raise ValueError("Uptodown URL is not valid")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            final_url = response.geturl()
            if not is_uptodown_url(final_url):
                raise ValueError("Uptodown redirected to an unsupported host")
            return final_url, response.read(2 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("Uptodown app page was not found") from exc
        if exc.code == 410:
            raise ValueError(
                "Uptodown is not serving its catalog from this environment right now"
            ) from exc
        raise ValueError(f"Uptodown returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"could not reach Uptodown: {exc}") from exc


def parse_uptodown_results(html: str, query: str) -> list[dict[str, str]]:
    parser = UptodownHTMLParser()
    parser.feed(html)
    query_terms = [term for term in re.split(r"\s+", query.lower().strip()) if term]
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        url = normalize_uptodown_url(link.get("href", ""))
        if not url or url in seen:
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname == "dw.uptodown.com":
            continue
        if "/android" not in parsed.path.lower() and not parsed.hostname.endswith(UPTODOWN_HOST_SUFFIX):
            continue
        title = unescape(link.get("title") or link.get("text") or "").strip()
        if not title:
            title = parsed.hostname.split(".")[0].replace("-", " ").title()
        haystack = f"{title} {url}".lower()
        if query_terms and not all(term in haystack for term in query_terms):
            continue
        seen.add(url)
        results.append(
            {
                "id": url,
                "title": title[:160],
                "url": url,
                "summary": "",
                "icon": "",
            }
        )
        if len(results) >= 24:
            break
    return results


def guessed_uptodown_app_url(query: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    return f"https://{slug}.en.uptodown.com/android"


def find_uptodown_download_url(page_url: str, html: str) -> str | None:
    parser = UptodownHTMLParser()
    parser.feed(html)
    candidates: list[tuple[int, str]] = []
    for link in parser.links:
        url = normalize_uptodown_url(link.get("href", ""), page_url)
        if not url:
            continue
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        text = f"{link.get('text', '')} {link.get('title', '')}".lower()
        if path.endswith(".apk") or parsed.hostname == "dw.uptodown.com":
            score = 0
        elif "/download" in path or "download" in text:
            score = 10
        else:
            continue
        candidates.append((score, url))

    # Some Uptodown pages embed the signed download URL in JSON rather than an
    # anchor, so cover that form without accepting arbitrary external URLs.
    for match in re.findall(
        r"""https?://[^"'\\\s<>]+(?:\.apk|dw\.uptodown\.com/[^"'\\\s<>]+)""",
        html,
        flags=re.IGNORECASE,
    ):
        url = normalize_uptodown_url(match.replace("\\/", "/"), page_url)
        if url:
            candidates.append((0, url))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], len(item[1])))
    return candidates[0][1]


def get_uptodown_app(url: str) -> dict[str, str]:
    normalized = normalize_uptodown_url(url)
    if not normalized:
        raise ValueError("Choose an app from Uptodown search results")
    final_url, html = fetch_uptodown_page(normalized)
    parser = UptodownHTMLParser()
    parser.feed(html)
    download_url = find_uptodown_download_url(final_url, html) or ""
    title = (
        parser.metas.get("og:title")
        or parser.metas.get("twitter:title")
        or Path(urllib.parse.urlparse(final_url).path).stem.replace("-", " ").title()
    )
    description = (
        parser.metas.get("description")
        or parser.metas.get("og:description")
        or ""
    )
    icon = ""
    for image in parser.images:
        normalized_image = normalize_uptodown_url(image, final_url)
        if normalized_image:
            icon = normalized_image
            break
    return {
        "id": final_url,
        "title": unescape(title).strip()[:160],
        "url": final_url,
        "summary": unescape(description).strip()[:300],
        "icon": icon,
        "apkUrl": download_url,
    }


def download_uptodown_app(app_url: str, destination: Path) -> tuple[str, int]:
    app = get_uptodown_app(app_url)
    apk_url = app.get("apkUrl", "")
    if not apk_url:
        raise ValueError("Uptodown did not expose a direct APK download for this app")
    return download_url(apk_url, destination)


def public_hostname(hostname: str) -> bool:
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve APK URL host: {exc}") from exc
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_multicast
            or parsed.is_unspecified
        ):
            raise ValueError("APK URL must point to a public host")
    return True


def download_url(url: str, destination: Path) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("APK URL must start with http:// or https://")
    public_hostname(parsed.hostname)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "APK-Intent-Patcher/1.0"},
    )
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
            final_hostname = urllib.parse.urlparse(response.geturl()).hostname
            if not final_hostname:
                raise ValueError("APK download did not return a valid host")
            public_hostname(final_hostname)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_APK_BYTES:
                    raise ValueError("APK is larger than the 650 MB upload limit")
                output.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"could not download APK URL: {exc}") from exc
    if total == 0:
        raise ValueError("APK URL returned an empty file")
    with destination.open("rb") as downloaded:
        if downloaded.read(4) != b"PK\x03\x04":
            destination.unlink(missing_ok=True)
            raise ValueError("downloaded URL did not return an APK file")
    return Path(urllib.parse.unquote(parsed.path)).name or "download.apk", total


class BuildJob:
    def __init__(
        self,
        job_id: str,
        directory: Path,
        source_name: str,
        component: str,
        launch_mode: str,
        signing_mode: str,
    ) -> None:
        self.id = job_id
        self.directory = directory
        self.input_path = directory / "input.apk"
        self.output_path = directory / f"{Path(source_name).stem}.external.apk"
        self.source_name = source_name
        self.component = component
        self.launch_mode = launch_mode
        self.signing_mode = signing_mode
        self.status = "queued"
        self.phase = "Waiting for a build slot"
        self.logs: list[str] = []
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self.lock = threading.RLock()

    def log(self, message: str) -> None:
        line = message.rstrip()
        if not line:
            return
        with self.lock:
            self.logs.append(line)
            if len(self.logs) > 1000:
                self.logs = self.logs[-1000:]
            with (self.directory / "build.log").open("a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "id": self.id,
                "status": self.status,
                "phase": self.phase,
                "sourceName": self.source_name,
                "component": self.component,
                "launchMode": self.launch_mode,
                "signingMode": self.signing_mode,
                "logs": list(self.logs),
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "error": self.error,
                "downloadUrl": f"/api/jobs/{self.id}/download"
                if self.status == "succeeded" and self.output_path.exists()
                else None,
                "outputName": self.output_path.name,
            }

    def run(self) -> None:
        with BUILD_LOCK:
            with self.lock:
                self.status = "running"
                self.phase = "Preparing Android toolchain"
                self.started_at = now_iso()
            self.log("Build queued. APK files stay inside this workspace.")
            command = [
                str(BUILD_SCRIPT),
                str(self.input_path),
                "--output",
                str(self.output_path),
                "--launch-mode",
                self.launch_mode,
                "--signing-mode",
                self.signing_mode,
            ]
            if self.component:
                command.extend(["--component", self.component])
            self.log("$ " + " ".join(command))
            try:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
                assert process.stdout is not None
                for line in process.stdout:
                    line = line.rstrip()
                    self.log(line)
                    if line.startswith("[build] decoding"):
                        self.phase = "Decoding APK"
                    elif line.startswith("[build] patching"):
                        self.phase = "Patching AndroidManifest.xml"
                    elif line.startswith("[build] rebuilding"):
                        self.phase = "Rebuilding APK"
                    elif line.startswith("[build] signing"):
                        self.phase = "Signing output"
                return_code = process.wait()
                if return_code != 0:
                    raise RuntimeError(f"build.sh exited with code {return_code}")
                if not self.output_path.exists():
                    raise RuntimeError("build completed without producing an APK")
                with self.lock:
                    self.status = "succeeded"
                    self.phase = "Ready to download"
                    self.finished_at = now_iso()
                self.log("Build complete. Download the patched APK below.")
            except Exception as exc:
                with self.lock:
                    self.status = "failed"
                    self.phase = "Build failed"
                    self.error = str(exc)
                    self.finished_at = now_iso()
                self.log(f"ERROR: {exc}")


def start_job(job: BuildJob) -> None:
    with JOBS_LOCK:
        JOBS[job.id] = job
    threading.Thread(target=job.run, name=f"apk-build-{job.id[:8]}", daemon=True).start()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "APKIntentDashboard/1.0"

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[dashboard] {self.address_string()} - {format_string % args}", flush=True)

    def send_json(self, value: object, status: int = HTTPStatus.OK) -> None:
        payload = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"error": message}, status)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self.send_json({"ok": True, "buildScript": BUILD_SCRIPT.exists()})
            return
        if path == "/api/uptodown/search":
            self.handle_uptodown_search(parsed)
            return
        if path == "/api/uptodown/app":
            self.handle_uptodown_app(parsed)
            return
        if path == "/favicon.ico":
            favicon = (
                b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
                b"<rect width='64' height='64' rx='14' fill='#101413'/>"
                b"<path d='M18 29 32 15l14 14-14 14z' fill='none' stroke='#d6f86c' stroke-width='4'/>"
                b"<path d='m25 36 14-14M25 28l11 11' stroke='#d6f86c' stroke-width='4'/>"
                b"</svg>"
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(favicon)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(favicon)
            return
        if path.startswith("/api/jobs/"):
            self.handle_job_get(path)
            return
        if path == "/" or path == "/index.html":
            self.serve_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            relative = Path(path.removeprefix("/static/"))
            if any(part in {"", ".", ".."} for part in relative.parts):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            mime = {
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
            }.get(relative.suffix, "application/octet-stream")
            self.serve_file(WEB_ROOT / relative, mime)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_uptodown_search(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0].strip()
        if len(query) < 2 or len(query) > 80:
            self.send_error_json("Enter at least 2 characters to search Uptodown")
            return
        encoded_query = urllib.parse.quote_plus(query)
        last_error = "Uptodown search returned no results"
        for template in UPTODOWN_SEARCH_URLS:
            search_url = template.format(query=encoded_query)
            try:
                _, html = fetch_uptodown_page(search_url)
                results = parse_uptodown_results(html, query)
                self.send_json({"source": "uptodown", "query": query, "results": results})
                return
            except ValueError as exc:
                last_error = str(exc)
        # Uptodown also publishes app pages on <slug>.en.uptodown.com. This
        # fallback keeps a one-word app lookup useful if their search route is
        # temporarily unavailable while avoiding fabricated catalog entries.
        try:
            guessed_url = guessed_uptodown_app_url(query)
            final_url, html = fetch_uptodown_page(guessed_url)
            app = get_uptodown_app(final_url)
            if app["title"]:
                self.send_json({"source": "uptodown", "query": query, "results": [app]})
                return
        except ValueError as exc:
            last_error = str(exc) if "not serving" not in str(exc).lower() else last_error
        self.send_error_json(last_error, HTTPStatus.BAD_GATEWAY)

    def handle_uptodown_app(self, parsed: urllib.parse.ParseResult) -> None:
        url = urllib.parse.parse_qs(parsed.query).get("url", [""])[0].strip()
        if not url:
            self.send_error_json("An Uptodown app URL is required")
            return
        try:
            self.send_json({"source": "uptodown", **get_uptodown_app(url)})
        except ValueError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_GATEWAY)

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        payload = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def handle_job_get(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) < 3 or not JOB_ID_RE.match(parts[2]):
            self.send_error_json("job not found", HTTPStatus.NOT_FOUND)
            return
        with JOBS_LOCK:
            job = JOBS.get(parts[2])
        if job is None:
            self.send_error_json("job not found", HTTPStatus.NOT_FOUND)
            return
        if len(parts) == 4 and parts[3] == "download":
            self.download_job(job)
            return
        self.send_json(job.snapshot())

    def download_job(self, job: BuildJob) -> None:
        if job.status != "succeeded" or not job.output_path.is_file():
            self.send_error_json("APK is not ready yet", HTTPStatus.NOT_FOUND)
            return
        file_size = job.output_path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.android.package-archive")
        self.send_header("Content-Length", str(file_size))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{safe_name(job.output_path.name)}"',
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with job.output_path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile)

    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/api/jobs":
            self.send_error_json("route not found", HTTPStatus.NOT_FOUND)
            return
        try:
            job = self.create_job()
        except ValueError as exc:
            self.send_error_json(str(exc))
            return
        start_job(job)
        self.send_json(job.snapshot(), HTTPStatus.ACCEPTED)

    def create_job(self) -> BuildJob:
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            raise ValueError("request is empty or larger than the 650 MB limit")
        body = self.rfile.read(content_length)
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("use multipart/form-data with an APK file or APK URL")
        envelope = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
            + body
        )
        message = BytesParser(policy=policy.default).parsebytes(envelope)
        fields: dict[str, str] = {}
        upload: bytes | None = None
        upload_name = "uploaded.apk"
        for part in message.iter_parts():
            disposition = part.get("Content-Disposition", "")
            field_name = part.get_param("name", header="content-disposition")
            if not field_name:
                continue
            if part.get_filename():
                upload = part.get_payload(decode=True) or b""
                upload_name = safe_name(part.get_filename())
            else:
                payload = part.get_payload(decode=True) or b""
                fields[field_name] = payload.decode("utf-8", "replace").strip()

        component = fields.get("component", "")
        launch_mode = fields.get("launch_mode", "singleTask")
        signing_mode = fields.get("signing_mode", "debug")
        apk_url = fields.get("apk_url", "")
        uptodown_app_url = fields.get("uptodown_app_url", "")
        if launch_mode not in ALLOWED_LAUNCH_MODES:
            raise ValueError("unsupported launch mode")
        if signing_mode not in ALLOWED_SIGNING_MODES:
            raise ValueError("unsupported signing mode")
        if uptodown_app_url and not is_uptodown_url(uptodown_app_url):
            raise ValueError("Uptodown app URL is invalid")
        if not upload and not apk_url and not uptodown_app_url:
            raise ValueError("choose an APK file or paste an APK URL")
        if upload and len(upload) > MAX_APK_BYTES:
            raise ValueError("APK is larger than the 650 MB upload limit")

        job_id = str(uuid.uuid4())
        directory = JOBS_ROOT / job_id
        directory.mkdir(parents=True, exist_ok=False)
        job = BuildJob(
            job_id,
            directory,
            upload_name if upload else (fields.get("source_name") or apk_url or "uptodown-app.apk"),
            component,
            launch_mode,
            signing_mode,
        )
        if upload:
            job.input_path.write_bytes(upload)
            job.log(f"Received upload: {upload_name} ({len(upload) / 1024 / 1024:.1f} MB)")
        else:
            if uptodown_app_url:
                job.log(f"Resolving APK from Uptodown: {uptodown_app_url}")
            else:
                job.log(f"Downloading APK from: {apk_url}")
            try:
                if uptodown_app_url:
                    remote_name, size = download_uptodown_app(uptodown_app_url, job.input_path)
                else:
                    remote_name, size = download_url(apk_url, job.input_path)
                job.source_name = safe_name(remote_name)
                job.log(f"Downloaded {job.source_name} ({size / 1024 / 1024:.1f} MB)")
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
        return job


def main() -> None:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"[dashboard] serving on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), DashboardHandler).serve_forever()


if __name__ == "__main__":
    main()