"""Running a generated project's own dev server, and keeping it alive.

A static preview is enough to look at a clone and useless for working on
one. `next dev` gives hot reloading, which turns "edit the file, reload the
iframe, lose your scroll position" into "edit the file, the page updates".
That is worth the machinery, and only worth it for the framework stack -
the static stack has no dev server, and inventing one would be a slower
way of serving files that already serve.

Two things this refuses to do, both on purpose:

It never runs `npm install`. That executes lifecycle scripts out of a
model-generated `package.json` and downloads hundreds of megabytes; it is
the user's machine and their call, so the absence of `node_modules` is
reported as something to do rather than quietly done.

It never runs anything but the project's own `dev` script. The script text
comes from a model, so the command is a fixed list, not a string the
caller supplies.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

# Where generated projects are written. Mirrors routes.local_project so a
# test can point both at one folder.
PROJECTS_DIR = Path(__file__).resolve().parent / "generated_sites"

# The one script a dev server may run, and what it may be asked for.
DEV_SCRIPT = "dev"

# How long to wait for the server to answer before calling it failed. Next's
# first compile is slow but not this slow; longer usually means a build
# error, which the captured output then explains.
STARTUP_TIMEOUT_SECONDS = 90
STARTUP_POLL_SECONDS = 0.5
# How long to wait for a kill to take. Generous, because ending a process
# tree on Windows is slow, but finite: an unbounded wait here is a hang with
# no error message and no way to interrupt it.
KILL_TIMEOUT_SECONDS = 20
# How much of the child's output to keep. Enough to read a build error,
# bounded because a chatty dev server would otherwise fill memory.
MAX_LOG_LINES = 400
# A file written into a running project.
MAX_WRITE_BYTES = 5_000_000

_WINDOWS = sys.platform.startswith("win")


class DevServerError(RuntimeError):
    """Something the user can act on: no node, no modules, no script."""


@dataclass
class DevServer:
    """One project's dev server, and what it has printed so far."""

    run_id: str
    root: Path
    port: int
    process: Optional[subprocess.Popen[bytes]] = None
    started_at: float = 0.0
    log: List[str] = field(default_factory=lambda: cast(List[str], []))
    error: str = ""
    # The thread draining the child's output. Set at start; joined on stop.
    reader: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def to_json(self) -> Dict[str, Any]:
        return {
            "runId": self.run_id,
            "running": self.running,
            "url": self.url if self.running else "",
            "startedAt": self.started_at,
            "log": self.log[-40:],
            "error": self.error,
        }


_servers: Dict[str, DevServer] = {}


def _project_root(run_id: str) -> Path:
    """The folder a run's project lives in, or a refusal.

    The name is validated rather than sanitised: a run id that does not look
    like one of ours has not been through our own save path, and this is
    about to become a working directory for a process.
    """
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise DevServerError("That is not a project id.")
    root = PROJECTS_DIR / run_id
    if not root.is_dir():
        raise DevServerError("That project is not saved on this server.")
    return root


def free_port() -> int:
    """A port nothing is listening on.

    Bound and released rather than picked from a range: a fixed base port
    makes the second dev server fail in a way that looks like the tool is
    broken.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _node_available() -> str:
    """The npm to run with, or a refusal the user can act on."""
    npm = shutil.which("npm")
    if npm is None:
        raise DevServerError(
            "Node.js is not on the PATH, so there is no dev server to run. "
            "The static preview still works."
        )
    return npm


def _scripts_of(root: Path) -> Dict[str, Any]:
    """The project's `scripts`, or an empty dict when it has no package.json."""
    import json

    manifest = root / "package.json"
    if not manifest.is_file():
        return {}
    try:
        raw: Any = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    scripts: Any = cast(Any, raw).get("scripts") if isinstance(raw, dict) else None
    if not isinstance(scripts, dict):
        return {}
    return {str(key): str(value) for key, value in cast(Dict[str, Any], scripts).items()}


def start(run_id: str) -> DevServer:
    """Start (or return) the dev server for a saved project.

    Synchronous, because starting a server and waiting for it to answer is
    what the caller asked for. The route that uses it runs it in a thread.
    """
    existing = _servers.get(run_id)
    if existing is not None and existing.running:
        return existing

    root = _project_root(run_id)
    npm = _node_available()
    scripts = _scripts_of(root)
    if DEV_SCRIPT not in scripts:
        # A static project has no dev server and does not need one.
        raise DevServerError("This project has no dev script - it is a static site.")
    if not (root / "node_modules").is_dir():
        raise DevServerError(
            "The project has no node_modules yet. Run `npm install` in the "
            "saved folder first - this will not run it for you."
        )

    port = free_port()
    # The script name is a constant, not a caller's string, and it is
    # passed as an argument rather than through a shell.
    command = [npm, "run", DEV_SCRIPT, "--", "--port", str(port)]
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if _WINDOWS else 0
    try:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise DevServerError(f"The dev server could not start: {exc}") from exc

    server = DevServer(run_id=run_id, root=root, port=port, process=process)
    server.started_at = time.time()
    _start_reader(server)
    _servers[run_id] = server
    return server


def _start_reader(server: DevServer) -> None:
    """Drain the child's output on a thread of its own.

    Reading the pipe inline would block: a dev server writes its ready line
    and then holds the pipe open for as long as it runs, so anything reading
    to end-of-file would sit there for the life of the process. This is the
    reason a started server can still be asked for its log afterwards.
    """
    process = server.process
    if process is None or process.stdout is None:
        return

    def read() -> None:
        assert process.stdout is not None
        try:
            for raw in iter(process.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    server.log.append(line)
                    if len(server.log) > MAX_LOG_LINES:
                        del server.log[: len(server.log) - MAX_LOG_LINES]
        except (OSError, ValueError):
            # The pipe closed with the process; `running` tells the story.
            return

    thread = threading.Thread(target=read, daemon=True, name=f"dev-server-{server.run_id}")
    thread.start()
    server.reader = thread


def wait_until_serving(
    server: DevServer, timeout: float = STARTUP_TIMEOUT_SECONDS
) -> bool:
    """Wait for the server to answer, or give up with the reason it gave.

    The reason matters more than the failure: `next dev` on a project with
    a type error prints the error and exits, and a bare "did not start"
    would send the user looking in the wrong place.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not server.running:
            # Give the reader a moment to land the line that explains why.
            if server.reader is not None:
                server.reader.join(timeout=0.5)
            server.error = _first_error(server.log) or "The dev server exited."
            return False
        try:
            with socket.create_connection(("127.0.0.1", server.port), timeout=1):
                return True
        except OSError:
            time.sleep(STARTUP_POLL_SECONDS)
    server.error = (
        f"The dev server did not answer within {int(timeout)} seconds."
    )
    return False


def _first_error(lines: List[str]) -> str:
    for line in lines:
        lowered = line.lower()
        if "error" in lowered or "failed" in lowered:
            return line.strip()
    return ""


def stop(run_id: str) -> bool:
    """Stop a running dev server. Returns whether there was one to stop."""
    server = _servers.get(run_id)
    if server is None or not server.running:
        _servers.pop(run_id, None)
        return False
    _terminate(server.process)
    if server.reader is not None:
        # The reader ends when the pipe closes; it is a daemon, so this is
        # about not leaving a thread behind, not about needing it.
        server.reader.join(timeout=2)
    server.process = None
    _servers.pop(run_id, None)
    return True


def _terminate(process: Optional[subprocess.Popen[bytes]]) -> None:
    """Kill the process and the children it started.

    `npm run dev` spawns `next dev`, which spawns workers. Killing only the
    parent leaves the port held by an orphan, and the next start then fails
    for a reason that has nothing to do with the user's project.
    """
    if process is None or process.poll() is not None:
        return
    if _WINDOWS:
        try:
            # Bounded, because this is the one call here with no natural
            # end: taskkill walks the process tree, and a child that will
            # not die keeps it walking. Waiting on it forever left the test
            # suite - and anything that stops a server - hanging with no
            # error and no way out.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=KILL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            # The parent is still ours to kill directly even if its tree
            # cannot be walked.
            process.kill()
    else:
        # Not on Windows, so the POSIX-only calls exist; pyright checks
        # against the configured platform, which is not the running one.
        killpg = getattr(os, "killpg", None)
        getpgid = getattr(os, "getpgid", None)
        try:
            if killpg is not None and getpgid is not None:
                killpg(getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def status(run_id: str) -> Optional[DevServer]:
    """The running server for a project, if there is one."""
    server = _servers.get(run_id)
    if server is None:
        return None
    return server


def running_servers() -> Dict[str, DevServer]:
    return {run_id: server for run_id, server in _servers.items() if server.running}


def write_file(run_id: str, path: str, content: str) -> str:
    """Write one file into a running project, so the dev server reloads it.

    This is the point of the whole module: the edit the user made in the
    editor lands on disk, and HMR does the rest. The path is resolved and
    checked against the project root, because a file write with a `..` in
    it is a file write outside the project.
    """
    root = _project_root(run_id)
    if server := status(run_id):
        # Refresh the log so the caller sees what the server just said.
        if server.reader is not None:
            server.reader.join(timeout=0.2)

    if not path or path.startswith("/") or "\\" in path:
        raise DevServerError("That file path is not one of the project's own.")
    target = (root / path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise DevServerError("That file is outside the project.") from exc
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise DevServerError("That file is too large to write.")

    target.parent.mkdir(parents=True, exist_ok=True)
    # Written through a temporary file and moved into place: a dev server
    # watching this path must never see half a file.
    temporary = target.with_name(target.name + ".urltocode-tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)
    return str(target.relative_to(root)).replace("\\", "/")


def stop_all() -> None:
    """Every dev server, on shutdown.

    A dev server outliving the backend would keep a port and a project
    watcher alive on a machine the user thinks they have closed.
    """
    for run_id in list(_servers):
        stop(run_id)


async def start_and_wait(run_id: str) -> DevServer:
    """Start a dev server without blocking the event loop."""
    return await asyncio.to_thread(_start_and_wait_sync, run_id)


def _start_and_wait_sync(run_id: str) -> DevServer:
    server = start(run_id)
    if server.running:
        wait_until_serving(server)
    return server
