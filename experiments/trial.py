"""A trial import, end to end, with nothing real at stake: a made-up mailbox, a new orbit vault served on its own port,
and a glean home of its own; then glean downloads, imports with the real agent, and prints each run's report. sift is
the running one: it is only searched, glean adds no collections to it (add_collections = false), and it refuses to
index the trial's files, which are in none of its collections.

    uv run python experiments/trial.py [--dir FOLDER] [--count N] [--port 8776] [--only 01-offer 02-reply]

Needs `orbit` on PATH, sift serving on its usual port, and Claude Code signed in. Each email is a real agent run.
The folder is kept, so the vault can be opened in Obsidian and the reports read again; give a new --dir for a new
trial."""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from make_mail import mailbox


def wait_for(port, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise SystemExit(f"orbit didn't start on port {port}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dir", type=Path, help="where to keep the trial (default: a new temporary folder)")
    p.add_argument("--count", type=int, default=10, help="emails to import, oldest first (default: %(default)s)")
    p.add_argument("--port", type=int, default=8776, help="the test vault's orbit port (default: %(default)s)")
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
        f'[orbit]\nurl = "http://127.0.0.1:{args.port}/mcp/write"\n\n[sift]\nadd_collections = false\n\n'
        f'[mail]\naddress = "tobias@example.com"\neml_folder = "{eml.as_posix()}"\n',
        encoding="utf-8",
    )
    env = os.environ | {"ORBIT_HOME": str(root / "orbit-home"), "GLEAN_HOME": str(home)}
    server = subprocess.Popen([orbit, "serve", str(vault), "--port", str(args.port)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    try:
        wait_for(args.port)
        glean = [sys.executable, "-m", "glean"]
        subprocess.run([*glean, "mail", "download"], env=env, check=True)
        subprocess.run([*glean, "mail", "run", "--oldest", str(args.count)], env=env)
        for report in sorted((home / "runs").rglob("report.md")):
            print(f"\n{'=' * 100}\n{report.parent.name}\n")
            print(report.read_text(encoding="utf-8").split("---\n", 2)[-1].strip())
    finally:
        server.terminate()
        server.wait(timeout=10)
    print(f"\nThe trial is in {root}: the vault in crm-test/, glean's home in home/.")


if __name__ == "__main__":
    main()
