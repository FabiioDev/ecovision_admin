"""Double write across two Firestore databases (primary + replica).

Every write is applied to every database. A write succeeds when at least one
database accepted it; the databases that failed get the operation queued in the
`_replication_pending` collection of a database that is up, and the queue is
replayed when the failed database answers again.

Reads go to the first healthy database (primary first) and fall back to the next
one on failure, so the app keeps working while either database is stopped or
deleted.

A database that comes back EMPTY (it was deleted and created again) cannot be
fixed by replaying the queue: it is copied in full from the other database
(`resync`) and kept out of reads until that copy finishes.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from google.api_core import exceptions as gexc
from google.cloud import firestore

log = logging.getLogger("ecovision.replication")

PENDING = "_replication_pending"
# Marker document: exists in every database the app has written to. A database
# without it is new or was recreated, so it needs a full copy.
MARKER = "_replication_meta/marker"


# --------------------------------------------------------------------------- #
# Operations: plain dicts so they can be stored in the pending queue.
# --------------------------------------------------------------------------- #
def op_set(path: str, data: dict, merge: bool = False) -> dict:
    return {"kind": "set", "path": path, "data": data, "merge": merge}


def op_create(path: str, data: dict) -> dict:
    return {"kind": "create", "path": path, "data": data}


def op_update(path: str, data: dict) -> dict:
    return {"kind": "update", "path": path, "data": data}


def op_delete(path: str) -> dict:
    return {"kind": "delete", "path": path}


def apply_op(client: firestore.Client, op: dict, strict: bool) -> None:
    """Apply one operation. `strict` keeps Firestore semantics (create fails if the
    document exists, update fails if it does not); the copies use the tolerant form
    so a replica that is behind never rejects a write the primary accepted."""
    ref = client.document(op["path"])
    kind = op["kind"]
    if kind == "delete":
        ref.delete()
    elif kind == "create" and strict:
        ref.create(op["data"])
    elif kind == "update" and strict:
        ref.update(op["data"])
    elif kind == "update":
        ref.set(_nest(op["data"]), merge=True)
    else:  # set, or tolerant create
        ref.set(op["data"], merge=bool(op.get("merge")) and kind == "set")


def _nest(data: dict) -> dict:
    """update() takes dotted field paths; set(merge=True) needs nested dicts."""
    out: dict = {}
    for key, value in data.items():
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def is_business_error(exc: Exception) -> bool:
    """Errors that describe the data, not the database: they are returned to the
    caller instead of taking the database out of rotation."""
    if isinstance(exc, gexc.AlreadyExists):
        return True
    if isinstance(exc, gexc.NotFound):
        return "database" not in str(exc).lower()
    return False


def is_missing_database(exc: Exception) -> bool:
    msg = str(exc).lower()
    return isinstance(exc, gexc.NotFound) and "database" in msg


# --------------------------------------------------------------------------- #
@dataclass
class Node:
    name: str           # "primary" or "replica"
    database: str
    client: firestore.Client
    down_until: float = 0.0
    last_error: str = ""
    stale: bool = False  # came back empty: excluded from reads until resynced
    stale_since: Optional[datetime] = None
    writes_ok: int = 0
    writes_failed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def up(self) -> bool:
        return time.monotonic() >= self.down_until

    def mark_down(self, exc: Exception, retry_s: int) -> None:
        self.down_until = time.monotonic() + retry_s
        self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        log.warning("database %s (%s) out of rotation: %s", self.name, self.database, self.last_error)


class Replicator:
    def __init__(self, nodes: list[Node], retry_s: int = 15):
        self.nodes = nodes
        self.retry_s = retry_s
        self._recover_lock = threading.Lock()
        self._last_recover = 0.0

    # ---------------- reads ---------------- #
    def read(self, fn: Callable[[firestore.Client], Any]) -> Any:
        """Run `fn` on the first usable database; on failure try the next one."""
        self._maybe_recover()
        candidates = ([n for n in self.nodes if n.up and not n.stale]
                      + [n for n in self.nodes if n.up and n.stale]
                      + [n for n in self.nodes if not n.up])
        last: Optional[Exception] = None
        for node in candidates:
            try:
                return fn(node.client)
            except Exception as exc:  # noqa: BLE001
                if is_business_error(exc):
                    raise
                node.mark_down(exc, self.retry_s)
                last = exc
        raise last or RuntimeError("no Firestore database configured")

    # ---------------- writes ---------------- #
    def write(self, *ops: dict) -> None:
        """Apply `ops` to every database. Raises only if no database accepted them."""
        self._maybe_recover()
        ok: list[Node] = []
        failed: list[Node] = []
        last: Optional[Exception] = None
        for node in self._write_order():
            if not node.up:
                failed.append(node)
                continue
            try:
                for op in ops:
                    apply_op(node.client, op, strict=not ok)
                ok.append(node)
                node.writes_ok += 1
            except Exception as exc:  # noqa: BLE001
                if not ok and is_business_error(exc):
                    raise  # e.g. create() on an existing admin: same as before
                node.mark_down(exc, self.retry_s)
                node.writes_failed += 1
                failed.append(node)
                last = exc
        if not ok:
            raise last or RuntimeError("no Firestore database available")
        for node in failed:
            self._enqueue(node, ops, ok)

    def transaction(self, fn: Callable[[firestore.Client, firestore.Transaction], tuple[Any, list[dict]]]) -> Any:
        """Run `fn(client, tx)` in a transaction on the first usable database. `fn`
        returns (result, ops); the ops are written inside the transaction there and
        then copied to the other databases like any other write."""
        self._maybe_recover()
        last: Optional[Exception] = None
        for node in self._write_order():
            if not node.up:
                continue
            try:
                @firestore.transactional
                def _run(tx: firestore.Transaction):
                    result, ops = fn(node.client, tx)
                    for op in ops:
                        ref = node.client.document(op["path"])
                        if op["kind"] == "delete":
                            tx.delete(ref)
                        else:
                            tx.set(ref, op["data"], merge=bool(op.get("merge")))
                    return result, ops

                result, ops = _run(node.client.transaction())
                node.writes_ok += 1
            except Exception as exc:  # noqa: BLE001
                if is_business_error(exc):
                    raise
                node.mark_down(exc, self.retry_s)
                node.writes_failed += 1
                last = exc
                continue
            for other in self.nodes:
                if other is node:
                    continue
                if not other.up:
                    self._enqueue(other, tuple(ops), [node])
                    continue
                try:
                    for op in ops:
                        apply_op(other.client, op, strict=False)
                    other.writes_ok += 1
                except Exception as exc:  # noqa: BLE001
                    other.mark_down(exc, self.retry_s)
                    other.writes_failed += 1
                    self._enqueue(other, tuple(ops), [node])
            return result
        raise last or RuntimeError("no Firestore database available")

    def _write_order(self) -> list[Node]:
        # A stale database still receives writes, but never decides them.
        return [n for n in self.nodes if not n.stale] + [n for n in self.nodes if n.stale]

    # ---------------- pending queue ---------------- #
    def _enqueue(self, target: Node, ops: tuple[dict, ...] | list[dict], holders: list[Node]) -> None:
        entry = {"target": target.database, "ops": list(ops), "seq": time.time_ns()}
        for holder in holders:
            try:
                holder.client.collection(PENDING).add(entry)
                return
            except Exception:  # noqa: BLE001
                log.exception("could not queue a pending write for %s in %s", target.database, holder.database)
        log.error("pending write for %s LOST: no database could store it", target.database)

    def pending_count(self, target: Node) -> Optional[int]:
        total = 0
        for holder in self.nodes:
            if holder is target or not holder.up:
                continue
            try:
                q = holder.client.collection(PENDING).where(
                    filter=firestore.FieldFilter("target", "==", target.database))
                total += int(q.count().get()[0][0].value)
            except Exception:  # noqa: BLE001
                return None
        return total

    # ---------------- recovery ---------------- #
    def _maybe_recover(self) -> None:
        """Probe databases whose retry window expired. Cheap: at most one probe per
        retry window, and only one request does it at a time."""
        now = time.monotonic()
        waiting = [n for n in self.nodes if n.down_until and n.up]
        stale = [n for n in self.nodes if n.stale]
        if not waiting and not stale:
            return
        if now - self._last_recover < 1.0 or not self._recover_lock.acquire(blocking=False):
            return
        try:
            self._last_recover = now
            for node in waiting:
                self._recover(node)
            for node in stale:
                if node.down_until:
                    continue
                if self._resynced_elsewhere(node):
                    continue
                self._start_resync(node)
        finally:
            self._recover_lock.release()

    @staticmethod
    def _set_stale(node: Node) -> None:
        node.stale = True
        node.stale_since = datetime.now(timezone.utc)

    def _resynced_elsewhere(self, node: Node) -> bool:
        """A full copy finished by another process (the manual command or another
        instance) also clears the stale flag. Needed on Cloud Run, where a
        background thread only gets CPU while a request is being served."""
        try:
            snap = node.client.document(MARKER).get()
        except Exception as exc:  # noqa: BLE001
            node.mark_down(exc, self.retry_s)
            return True
        done = snap.get("resynced_at") if snap.exists and "resynced_at" in (snap.to_dict() or {}) else None
        if done and node.stale_since and done >= node.stale_since:
            node.stale = False
            node.last_error = ""
            log.info("database %s was resynced by another process", node.database)
            return True
        return False

    def _recover(self, node: Node) -> None:
        try:
            has_marker = node.client.document(MARKER).get().exists
        except Exception as exc:  # noqa: BLE001
            node.mark_down(exc, self.retry_s)
            return
        if not has_marker:
            log.warning("database %s came back empty: full copy needed", node.database)
            self._set_stale(node)
            node.down_until = 0.0
            self._start_resync(node)
            return
        try:
            self.drain(node)
        except Exception as exc:  # noqa: BLE001
            node.mark_down(exc, self.retry_s)
            return
        node.down_until = 0.0
        node.last_error = ""
        log.info("database %s back in rotation", node.database)

    def drain(self, target: Node) -> int:
        """Replay the queued writes for `target`, oldest first. Returns how many."""
        entries = []
        for holder in self.nodes:
            if holder is target or not holder.up:
                continue
            q = holder.client.collection(PENDING).where(
                filter=firestore.FieldFilter("target", "==", target.database))
            entries += [(d.to_dict()["seq"], d.reference, d.to_dict()["ops"]) for d in q.stream()]
        entries.sort(key=lambda e: e[0])
        for _seq, ref, ops in entries:
            for op in ops:
                apply_op(target.client, op, strict=False)
            ref.delete()
        if entries:
            log.info("replayed %d pending writes into %s", len(entries), target.database)
        return len(entries)

    def _start_resync(self, node: Node) -> None:
        if not node.lock.acquire(blocking=False):
            return  # already running

        def _run():
            try:
                source = next(n for n in self.nodes if n is not node and n.up and not n.stale)
                copied = resync(source.client, node.client)
                # queued writes are already contained in the full copy
                for holder in self.nodes:
                    if holder is node:
                        continue
                    q = holder.client.collection(PENDING).where(
                        filter=firestore.FieldFilter("target", "==", node.database))
                    for d in q.stream():
                        d.reference.delete()
                node.stale = False
                node.last_error = ""
                log.info("database %s resynced from %s (%d documents)", node.database, source.database, copied)
            except StopIteration:
                log.error("no healthy database to resync %s from", node.database)
            except Exception as exc:  # noqa: BLE001
                node.mark_down(exc, self.retry_s)
            finally:
                node.lock.release()

        threading.Thread(target=_run, name=f"resync-{node.name}", daemon=True).start()

    # ---------------- status ---------------- #
    def status(self) -> list[dict]:
        return [{
            "name": n.name,
            "database": n.database,
            "state": "caida" if not n.up else ("sincronizando" if n.stale else "activa"),
            "writes_ok": n.writes_ok,
            "writes_failed": n.writes_failed,
            "pending": self.pending_count(n),
            "last_error": n.last_error,
        } for n in self.nodes]

    def ensure_markers(self) -> None:
        """At startup: a database without the marker while another one has it is new
        or was recreated, so it is resynced. On the very first run (no marker
        anywhere) the primary is the source of truth and gets the marker."""
        has: dict[str, bool] = {}
        for n in self.nodes:
            try:
                has[n.name] = n.client.document(MARKER).get().exists
            except Exception as exc:  # noqa: BLE001
                n.mark_down(exc, self.retry_s)
        if not any(has.values()):
            primary = self.nodes[0]
            if primary.name in has:
                primary.client.document(MARKER).set({"database": primary.database}, merge=True)
                has[primary.name] = True
        for n in self.nodes:
            if has.get(n.name) is False:
                self._set_stale(n)
                self._start_resync(n)


# --------------------------------------------------------------------------- #
def _walk(collection_ref, skip: set[str]):
    """Yield (path, data) of every document under `collection_ref`, recursively."""
    for doc in collection_ref.stream():
        yield doc.reference.path, doc.to_dict()
        for sub in doc.reference.collections():
            if sub.id not in skip:
                yield from _walk(sub, skip)
    # documents that only hold subcollections do not appear in stream()
    for ref in collection_ref.list_documents():
        snap = ref.get()
        if not snap.exists:
            for sub in ref.collections():
                if sub.id not in skip:
                    yield from _walk(sub, skip)


def resync(source: firestore.Client, target: firestore.Client) -> int:
    """Make `target` an exact copy of `source` (except the pending queue)."""
    skip = {PENDING}
    src: dict[str, dict] = {}
    for coll in source.collections():
        if coll.id not in skip:
            for path, data in _walk(coll, skip):
                src[path] = data
    batch, n = target.batch(), 0
    for path, data in src.items():
        batch.set(target.document(path), data)
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = target.batch()
    batch.commit()
    # remove what the source no longer has
    extra = []
    for coll in target.collections():
        if coll.id not in skip:
            extra += [p for p, _ in _walk(coll, skip) if p not in src]
    for path in extra:
        target.document(path).delete()
    target.document(MARKER).set({"resynced_at": firestore.SERVER_TIMESTAMP}, merge=True)
    return len(src)


# --------------------------------------------------------------------------- #
_instance: Optional[Replicator] = None
_instance_lock = threading.Lock()


def build(project: Optional[str], primary: str, replica: str, retry_s: int) -> Replicator:
    nodes = [Node("primary", primary, firestore.Client(project=project, database=primary))]
    if replica:
        nodes.append(Node("replica", replica, firestore.Client(project=project, database=replica)))
    rep = Replicator(nodes, retry_s)
    rep.ensure_markers()
    return rep


def get_replicator() -> Replicator:
    global _instance
    with _instance_lock:
        if _instance is None:
            from ..core.config import get_settings
            s = get_settings()
            _instance = build(s.google_cloud_project, s.firestore_database,
                              s.firestore_replica_database, s.firestore_retry_s)
        return _instance
