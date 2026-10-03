"""The dev server, with a real process behind it.

Everything else in test_dev_server.py is a refusal or a refusal's neighbour.
This is the one that proves the thing starts, answers on the port it was
given, picks up a live file write, and can still be stopped afterwards -
because a manager of imaginary processes proves nothing about one that
exists.

Skipped when Node.js is not installed, which is the user's call about
their machine and not a failure of the code.
"""

import json
import shutil
from pathlib import Path
from typing import Iterator

import pytest

import dev_server


@pytest.fixture(autouse=True)
def projects_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    folder = tmp_path / "generated_sites"
    folder.mkdir()
    monkeypatch.setattr(dev_server, "PROJECTS_DIR", folder)
    monkeypatch.setattr(dev_server, "_servers", {})
    yield folder
    dev_server.stop_all()


@pytest.fixture()
def live_server_script(tmp_path: Path) -> Path:
    """A dev script that serves a directory and notices a change.

    Stands in for `next dev`: binds a port, answers HTTP, and reports the
    file it last read so the test can see a hot reload actually happen.
    """
    script = tmp_path / "fake-dev.mjs"
    script.write_text(
        """
import { createServer } from "node:http";
import { readFileSync, existsSync } from "node:fs";

const flag = process.argv.indexOf("--port");
const port = Number(flag === -1 ? 0 : process.argv[flag + 1]);
let last = "";
createServer((req, res) => {
  if (existsSync("index.html")) last = readFileSync("index.html", "utf8");
  res.writeHead(200, { "content-type": "text/html" });
  res.end(last || "not written yet");
}).listen(port, () => console.log("ready - started server"));
""",
        encoding="utf-8",
    )
    return script


def make_project(projects_dir: Path, script: Path, run_id: str = "run-1") -> Path:
    root = projects_dir / run_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / ".keep").write_text("")
    # A `dev` script that runs our own process, so the test needs no
    # framework and no install.
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "site",
                "scripts": {"dev": f"node {script}"},
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_a_dev_server_starts_answers_and_stops(
    projects_dir: Path, live_server_script: Path
) -> None:
    make_project(projects_dir, live_server_script)

    server = dev_server.start("run-1")
    try:
        assert dev_server.wait_until_serving(server, timeout=30), server.log

        import urllib.request

        with urllib.request.urlopen(server.url, timeout=5) as response:
            body = response.read().decode("utf-8")
        # A server that answers before the file exists is running; one that
        # only answers because we seeded it would hide a real failure.
        assert body == "not written yet"
    finally:
        assert dev_server.stop("run-1") is True
    assert dev_server.status("run-1") is None


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_a_live_edit_reaches_the_running_server(
    projects_dir: Path, live_server_script: Path
) -> None:
    # This is the feature: an edit in the editor lands on disk, and the
    # running server serves it without being restarted.
    make_project(projects_dir, live_server_script)
    server = dev_server.start("run-1")
    try:
        assert dev_server.wait_until_serving(server, timeout=30), server.log

        dev_server.write_file("run-1", "index.html", "<h1>edited live</h1>")

        import urllib.request

        with urllib.request.urlopen(server.url, timeout=5) as response:
            assert "edited live" in response.read().decode("utf-8")
    finally:
        dev_server.stop("run-1")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_two_dev_servers_do_not_collide_on_one_port(
    projects_dir: Path, live_server_script: Path
) -> None:
    make_project(projects_dir, live_server_script)
    make_project(projects_dir, live_server_script, run_id="run-2")

    first = dev_server.start("run-1")
    second = dev_server.start("run-2")
    try:
        assert first.port != second.port
    finally:
        dev_server.stop_all()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_a_dev_server_that_cannot_bind_reports_rather_than_hanging(
    projects_dir: Path, live_server_script: Path
) -> None:
    # A project whose dev script exits immediately must come back with a
    # reason, not leave the caller waiting on a port nothing is on.
    root = make_project(projects_dir, live_server_script)
    (root / "package.json").write_text(
        json.dumps({"name": "site", "scripts": {"dev": "node -e console.error('Error: boom')"}}),
        encoding="utf-8",
    )

    server = dev_server.start("run-1")

    assert dev_server.wait_until_serving(server, timeout=20) is False
    assert "Error: boom" in server.error or "exited" in server.error


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_stopping_the_backend_stops_every_dev_server(
    projects_dir: Path, live_server_script: Path
) -> None:
    make_project(projects_dir, live_server_script)
    server = dev_server.start("run-1")
    assert dev_server.wait_until_serving(server, timeout=30), server.log

    dev_server.stop_all()

    # A dev server outliving the backend would hold a port and a file
    # watcher on a machine the user believes they have closed.
    assert dev_server.running_servers() == {}
