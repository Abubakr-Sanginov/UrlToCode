"""Running a generated project's dev server.

The rules under test are the ones that keep this safe to offer: it runs
only the project's own `dev` script, only for a project we saved, and it
never runs `npm install` for the user. The rest is whether a live file
write actually lands where a file watcher will see it.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest

import dev_server
from dev_server import DevServer, DevServerError


@pytest.fixture(autouse=True)
def projects_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project folder per test, never the developer's own projects."""
    folder = tmp_path / "generated_sites"
    folder.mkdir()
    monkeypatch.setattr(dev_server, "PROJECTS_DIR", folder)
    monkeypatch.setattr(dev_server, "_servers", {})
    return folder


@pytest.fixture()
def no_npm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend Node.js is not installed, so no test needs it."""
    monkeypatch.setattr(dev_server.shutil, "which", lambda _: None)


def make_project(
    projects_dir: Path,
    run_id: str = "run-1",
    scripts: Any = None,
    modules: bool = True,
) -> Path:
    root = projects_dir / run_id
    root.mkdir(parents=True, exist_ok=True)
    if modules:
        (root / "node_modules").mkdir()
        (root / "node_modules" / ".keep").write_text("")
    declared: Dict[str, Any] = {"dev": "next dev"} if scripts is None else scripts
    manifest: Dict[str, Any] = {"name": "site", "scripts": declared}
    (root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


# --- what it will and will not run ------------------------------------------


def test_a_static_project_is_refused_with_a_reason(
    projects_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No package.json at all: there is no dev server, and inventing one
    # would be a slower way of serving files that already serve.
    (projects_dir / "static").mkdir()
    monkeypatch.setattr(dev_server, "_node_available", lambda: "npm")

    with pytest.raises(DevServerError, match="static site"):
        dev_server.start("static")


def test_a_project_with_no_dev_script_is_refused(
    projects_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_project(projects_dir, scripts={"build": "next build"})
    monkeypatch.setattr(dev_server, "_node_available", lambda: "npm")

    with pytest.raises(DevServerError, match="no dev script"):
        dev_server.start("run-1")


def test_npm_install_is_never_run_for_the_user(
    projects_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # That executes lifecycle scripts out of a model-generated package.json
    # and downloads hundreds of megabytes. It is the user's call.
    make_project(projects_dir, modules=False)
    monkeypatch.setattr(dev_server, "_node_available", lambda: "npm")

    with pytest.raises(DevServerError, match="npm install"):
        dev_server.start("run-1")


def test_a_machine_without_node_says_so_plainly(
    projects_dir: Path, no_npm: None
) -> None:
    make_project(projects_dir)

    with pytest.raises(DevServerError, match="Node.js is not on the PATH"):
        dev_server.start("run-1")


def test_only_the_dev_script_is_ever_run(
    projects_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The script text comes from a model, so the command is a fixed list and
    # not a string the caller supplies.
    make_project(projects_dir)
    monkeypatch.setattr(dev_server, "_node_available", lambda: "npm")
    recorded: dict[str, Any] = {}

    class FakeProcess:
        pid = 4321
        stdout = None

        def poll(self) -> int | None:
            return 0

    def fake_popen(command: list[str], **kwargs: Any) -> Any:
        recorded["command"] = command
        recorded["cwd"] = kwargs.get("cwd")
        return FakeProcess()

    monkeypatch.setattr(dev_server.subprocess, "Popen", fake_popen)
    dev_server.start("run-1")

    assert recorded["command"][:3] == ["npm", "run", "dev"]
    assert recorded["cwd"] == str(projects_dir / "run-1")


# --- whose projects it will touch -------------------------------------------


@pytest.mark.parametrize("run_id", ["../escape", "..\\escape", ".hidden", ""])
def test_a_run_id_that_does_not_look_like_one_is_refused(
    projects_dir: Path, run_id: str
) -> None:
    # This path becomes a working directory for a process.
    with pytest.raises(DevServerError):
        dev_server.write_file(run_id, "index.html", "x")


def test_a_project_that_was_never_saved_is_refused(projects_dir: Path) -> None:
    with pytest.raises(DevServerError, match="not saved"):
        dev_server.write_file("never-saved", "index.html", "x")


# --- live editing ------------------------------------------------------------


def test_an_edit_lands_in_the_project_where_a_watcher_can_see_it(
    projects_dir: Path,
) -> None:
    # The whole point: the user's edit reaches disk, the framework rebuilds.
    root = make_project(projects_dir)

    written = dev_server.write_file("run-1", "app/page.tsx", "export default 1")

    assert written == "app/page.tsx"
    assert (root / "app" / "page.tsx").read_text(encoding="utf-8") == "export default 1"


def test_a_write_outside_the_project_is_refused(projects_dir: Path) -> None:
    make_project(projects_dir)

    with pytest.raises(DevServerError, match="outside the project"):
        dev_server.write_file("run-1", "../escaped.html", "x")


def test_a_write_with_an_absolute_path_is_refused(projects_dir: Path) -> None:
    make_project(projects_dir)

    with pytest.raises(DevServerError, match="project's own"):
        dev_server.write_file("run-1", "/etc/passwd", "x")


def test_a_write_with_no_leftover_temporary_file(projects_dir: Path) -> None:
    # A watcher that sees a half-written file rebuilds a broken page.
    root = make_project(projects_dir)

    dev_server.write_file("run-1", "index.html", "<html></html>")

    assert [p.name for p in root.iterdir() if p.is_file() and p.name != "package.json"] == [
        "index.html"
    ]


def test_an_oversized_write_is_refused(projects_dir: Path) -> None:
    make_project(projects_dir)

    with pytest.raises(DevServerError, match="too large"):
        dev_server.write_file("run-1", "big.html", "x" * (dev_server.MAX_WRITE_BYTES + 1))


# --- lifecycle ---------------------------------------------------------------


def test_a_project_with_no_server_reports_nothing_running(projects_dir: Path) -> None:
    assert dev_server.status("run-1") is None


def test_stopping_a_server_that_never_ran_says_so(projects_dir: Path) -> None:
    assert dev_server.stop("run-1") is False


def test_a_server_that_has_exited_is_not_still_running(projects_dir: Path) -> None:
    server = DevServer(run_id="run-1", root=projects_dir, port=3000)
    server.process = _exited()
    dev_server._servers["run-1"] = server

    assert server.running is False
    assert server.to_json()["url"] == ""


def test_a_kill_that_will_not_finish_does_not_hang_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # taskkill walks the process tree and has no natural end: a child that
    # will not die keeps it walking. An unbounded wait here hung the whole
    # test suite once for eight hours with no error to show for it.
    def never_returns(*_args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="taskkill", timeout=1)

    monkeypatch.setattr(dev_server.subprocess, "run", never_returns)
    process = _alive()

    dev_server._terminate(process)

    # The parent is still ours to kill even when its tree cannot be walked.
    assert process.killed is True


def test_a_kill_is_always_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    # Whatever else happens, the call that ends a server gets a deadline.
    seen: Dict[str, Any] = {}

    def capture(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return None

    monkeypatch.setattr(dev_server.subprocess, "run", capture)

    dev_server._terminate(_alive())

    assert seen.get("timeout") is not None


def test_a_failed_start_reports_what_the_server_printed(projects_dir: Path) -> None:
    # `next dev` on a broken project prints the error and exits; a bare "did
    # not start" would send the user looking in the wrong place.
    server = DevServer(run_id="run-1", root=projects_dir, port=3000)
    server.process = _exited()
    server.log = ["ready - started server", "Error: page.tsx:12 bad type"]

    assert dev_server.wait_until_serving(server, timeout=0.1) is False
    assert "page.tsx:12" in server.error


def test_a_port_is_chosen_that_nothing_is_using() -> None:
    port = dev_server.free_port()

    assert 1024 < port < 65536


def _exited() -> Any:
    class Dead:
        pid = 1234
        stdout = None

        def poll(self) -> int:
            return 1

    return Dead()


def _alive() -> Any:
    """A process that looks like it is still running, so the kill path runs."""

    class Running:
        pid = 1234
        stdout = None
        killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: Any = None) -> int:
            return 0

    return Running()
