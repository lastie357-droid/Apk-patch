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
        if launch_mode not in ALLOWED_LAUNCH_MODES:
            raise ValueError("unsupported launch mode")
        if signing_mode not in ALLOWED_SIGNING_MODES:
            raise ValueError("unsupported signing mode")
        if not upload and not apk_url:
            raise ValueError("choose an APK file or paste an APK URL")
        if upload and len(upload) > MAX_APK_BYTES:
            raise ValueError("APK is larger than the 650 MB upload limit")

        job_id = str(uuid.uuid4())
        directory = JOBS_ROOT / job_id
        directory.mkdir(parents=True, exist_ok=False)
        job = BuildJob(
            job_id,
            directory,
            upload_name if upload else apk_url,
            component,
            launch_mode,
            signing_mode,
        )
        if upload:
            job.input_path.write_bytes(upload)
            job.log(f"Received upload: {upload_name} ({len(upload) / 1024 / 1024:.1f} MB)")
        else:
            job.log(f"Downloading APK from: {apk_url}")
            try:
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