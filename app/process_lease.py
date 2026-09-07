"""Single-host process ownership for browser connections and in-memory quotas."""
from pathlib import Path
import os


class SingleProcessLease:
    """Hold an OS lock for the runtime lifetime; never unlink a live lock file."""

    def __init__(self, database_path: str):
        if database_path == ":memory:":
            raise ValueError("Production requires a durable database path")
        self.path = Path(database_path).expanduser().resolve().with_suffix(".runtime.lock")
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                "FlowProviderAPI supports one process per database/connector runtime. "
                "Stop the other instance; do not use multiple workers or replicas."
            ) from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()  # OS releases the lock, including on process exit.
            self._handle = None
