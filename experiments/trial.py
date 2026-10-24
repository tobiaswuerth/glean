"""A trial import, end to end, with nothing real at stake: a made-up mailbox, a new orbit vault and a sift of its own,
each served on its own port with its home in the trial's folder, and a glean home of its own; then glean downloads,
imports with the real agent, and prints each run's report.

    uv run python experiments/trial.py [--dir FOLDER] [--count N] [--port 8776] [--only 01-offer 02-reply]

Needs `orbit` and `sift` on PATH and Claude Code signed in. Each email is a real agent run. The folder is kept, so the
vault can be opened in Obsidian and the reports read again; give a new --dir for a new trial."""

import argparse
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from make_mail import mailbox


def listening(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for(port, seconds=30, name="orbit"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if listening(port):
            return
        time.sleep(0.3)
    raise SystemExit(f"{name} didn't start on port {port}")


def settings(orbit_port, sift_port):
    """glean.toml's tables for servers on these ports."""
    return (f'[orbit]\nurl = "http://127.0.0.1:{orbit_port}/mcp/write"\n\n'
            f'[sift]\nurl = "http://127.0.0.1:{sift_port}/mcp"\n\n')  # fmt: skip


@contextlib.contextmanager
def serving(root, vault, orbit, port, sift_port):
    """orbit serving the vault on `port`, and a sift of the trial's own on `sift_port`, each with its home under
    `root`, while the block runs: the environment for glean, whose home is root/home. The owner's servers and indexes
    are never touched."""
    env = os.environ | {"ORBIT_HOME": str(root / "orbit-home"), "SIFT_HOME": str(root / "sift-home"),
                        "GLEAN_HOME": str(root / "home")}  # fmt: skip
    for taken in (p for p in (port, sift_port) if listening(p)):  # else that server answers, not ours
        raise SystemExit(f"port {taken} is in use: stop what serves it, or give another port")
    sift = shutil.which("sift") or sys.exit("sift isn't on PATH")
    started = []
    try:
        for cmd, at, name in (([orbit, "serve", str(vault), "--port", str(port)], port, "orbit"),
                              ([sift, "serve", "--port", str(sift_port)], sift_port, "sift")):  # fmt: skip
            started.append(subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(at, 120, name)
        yield env
    finally:
        for server in started:
            server.terminate()
        for server in started:
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:  # a server that doesn't end on its own is ended
                server.kill()
                server.wait()


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dir", type=Path, help="where to keep the trial (default: a new temporary folder)")
    p.add_argument("--count", type=int, default=10, help="emails to import, oldest first (default: %(default)s)")
    p.add_argument("--port", type=int, default=8776, help="the test vault's orbit port (default: %(default)s)")
    p.add_argument("--sift-port", type=int, default=8779, help="the trial's own sift's port (default: %(default)s)")
    p.add_argument("--only", nargs="*", help="only the made-up emails whose file names start with these")
    args = p.parse_args()
    root = (args.dir or Path(tempfile.mkdtemp(prefix="glean-trial-"))).resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"{root} isn't empty: give a new --dir")
    eml, vault, home = root / "eml", root / "crm-test", root / "home"
    for name, raw in mailbox("tobias@example.com").items():
        if args.only and not any(Path(name).name.startswith(o) for o in args.only):
            continue
        path = eml / name.replace("Inbox/", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    orbit = shutil.which("orbit") or sys.exit("orbit isn't on PATH")
    subprocess.run([orbit, "init", str(vault), "--name", "Tobias Würth", "--email", "tobias@example.com"], check=True,
                   capture_output=True)  # fmt: skip
    home.mkdir(parents=True)
    (home / "glean.toml").write_text(
        settings(args.port, args.sift_port)
        + f'[mail]\naddress = "tobias@example.com"\neml_folder = "{eml.as_posix()}"\n',
        encoding="utf-8",
    )
    with serving(root, vault, orbit, args.port, args.sift_port) as env:
        glean = [sys.executable, "-m", "glean"]
        subprocess.run([*glean, "mail", "download"], env=env, check=True)
        subprocess.run([*glean, "mail", "run", "--oldest", str(args.count)], env=env)
    for report in sorted((home / "runs").rglob("report.md")):
        print(f"\n{'=' * 100}\n{report.parent.name}\n")
        print(report.read_text(encoding="utf-8").split("---\n", 2)[-1].strip())
    print(f"\nThe trial is in {root}: the vault in crm-test/, glean's home in home/.")


if __name__ == "__main__":
    main()
