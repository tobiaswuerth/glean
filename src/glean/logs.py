"""Each download, run and review keeps what glean said in a log of its own, every line with its time:
GLEAN_HOME/logs/mail-run-2026-10-23_210509.log. The newest KEEP logs of each kind are kept."""

import contextlib
import logging

KEEP = 100
FORMAT = "%(asctime)s %(levelname)s %(message)s"


@contextlib.contextmanager
def kept(home, name, now):
    """Log what glean says while the block runs, and how it ended if it failed, to a new file; yields its path."""
    folder = home / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{name}-{now:%Y-%m-%d_%H%M%S}"
    path = next(p for n in range(1, 1000) if not (p := folder / (stem + (f"-{n}" if n > 1 else "") + ".log")).exists())
    _prune(folder, name)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    log, level = logging.getLogger("glean"), logging.getLogger("glean").level
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        yield path
    except KeyboardInterrupt:
        log.error("stopped")
        raise
    except Exception as e:
        log.error("%s: %s", type(e).__name__, e)
        raise
    finally:
        log.removeHandler(handler)
        log.setLevel(level)
        handler.close()


def _prune(folder, name):
    """Delete the oldest logs of this kind, so that with the new one KEEP are left."""
    logs = sorted(folder.glob(f"{name}-????-??-??_*.log"))
    for old in logs[: max(0, len(logs) - KEEP + 1)]:
        old.unlink(missing_ok=True)
