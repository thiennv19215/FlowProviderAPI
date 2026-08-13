from __future__ import annotations


class ExtensionManager:
    """Tracks only the lifecycle state needed by the live gateway socket."""

    def __init__(self, bridge):
        self.bridge = bridge

    @staticmethod
    def connected(conn) -> None:
        conn.suspect_since = None

    @staticmethod
    def heartbeat(conn) -> None:
        conn.suspect_since = None

    @staticmethod
    def suspect(conn) -> None:
        if conn.suspect_since is None:
            conn.suspect_since = __import__("time").time()

    @staticmethod
    def disconnected(_conn) -> None:
        return None
