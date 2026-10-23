"""Each download, run and review keeps what glean said in a log of its own, every line with its time:
GLEAN_HOME/logs/mail-run-2026-10-23_210509.log. Every log is kept."""

import contextlib
import logging

FORMAT = "%(asctime)s %(levelname)s %(message)s"


@contextlib.contextmanager
def kept(home, name, now):
    """Log what glean says while the block runs, and how it ended if it failed, to a new file; yields its path."""
    folder = home / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{name}-{now:%Y-%m-%d_%H%M%S}"
    path = next(p for n in range(1, 1000) if not (p := folder / (stem + (f"-{n}" if n > 1 else "") + ".log")).exists())
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
