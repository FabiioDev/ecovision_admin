"""Double write across two Firestore databases, against the Firestore emulator.

Run with the emulator up (see docs/ALTA_DISPONIBILIDAD.md):
    docker run -d --name fs-emu -p 8085:8085 \
      gcr.io/google.com/cloudsdktool/google-cloud-cli:585.0.0-emulators \
      gcloud emulators firestore start --host-port=0.0.0.0:8085
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 python -m pytest tests/test_replication.py

A deleted database is simulated with a client that answers exactly what Firestore
answers for a deleted database (NotFound "database ... does not exist").
"""

import os
import time
import uuid

import pytest
from google.api_core import exceptions as gexc

pytestmark = pytest.mark.skipif(not os.environ.get("FIRESTORE_EMULATOR_HOST"),
                                reason="needs the Firestore emulator (FIRESTORE_EMULATOR_HOST)")

from google.cloud import firestore  # noqa: E402

from app.flags.catalog import AppConfig  # noqa: E402
from app.services import admins, audit, config_store, replication  # noqa: E402

RETRY_S = 1


class DeletedDatabase:
    """Any call raises the error Firestore returns for a deleted database."""

    def __init__(self, name):
        self.name = name

    def _fail(self, *_a, **_k):
        raise gexc.NotFound(f"The database {self.name} does not exist for project p")

    def __getattr__(self, _attr):
        return self

    def __call__(self, *a, **k):
        self._fail()


@pytest.fixture
def rep():
    project = f"t{uuid.uuid4().hex[:10]}"
    r = replication.build(project, "(default)", "replica", RETRY_S)
    replication._instance = r
    yield r
    replication._instance = None


def primary(r):
    return r.nodes[0]


def replica(r):
    return r.nodes[1]


def exists(node_client, path):
    return node_client.document(path).get().exists


def wait_recovery():
    time.sleep(RETRY_S + 0.2)


def kill(node):
    node._real = node.client
    node.client = DeletedDatabase(node.database)


def revive(node, client=None):
    node.client = client or node._real


# ---------------------------------------------------------------- both up -- #
def test_write_lands_in_both_databases(rep):
    admins.create("ana", "contrasena-larga-1", "lector")
    assert exists(primary(rep).client, "admins/ana")
    assert exists(replica(rep).client, "admins/ana")


def test_audit_entry_has_same_id_in_both(rep):
    audit.record("ana", "admin.login")
    a = [d.id for d in primary(rep).client.collection("audit_log").stream()]
    b = [d.id for d in replica(rep).client.collection("audit_log").stream()]
    assert a == b and len(a) == 1


def test_business_errors_still_reach_the_caller(rep):
    admins.create("ana", "contrasena-larga-1", "lector")
    with pytest.raises(gexc.AlreadyExists):
        admins.create("ana", "contrasena-larga-1", "lector")
    assert all(n.up for n in rep.nodes)  # a data error does not take a database down


# ------------------------------------------------------------ replica dies -- #
def test_replica_deleted_app_keeps_writing_and_catches_up(rep):
    kill(replica(rep))
    admins.create("ana", "contrasena-larga-1", "lector")
    admins.record_login("ana")
    assert admins.get("ana") is not None
    assert not replica(rep).up
    assert rep.pending_count(replica(rep)) == 2

    revive(replica(rep))
    wait_recovery()
    admins.get("ana")  # any request probes the database again
    assert replica(rep).up
    assert exists(replica(rep).client, "admins/ana")
    assert replica(rep).client.document("admins/ana").get().get("last_login_at") is not None
    assert rep.pending_count(replica(rep)) == 0


# ------------------------------------------------------------ primary dies -- #
def test_primary_deleted_reads_and_writes_use_the_replica(rep):
    admins.create("ana", "contrasena-larga-1", "lector")
    kill(primary(rep))
    assert admins.get("ana").username == "ana"          # read falls back
    admins.create("beto", "contrasena-larga-2", "editor")  # write still works
    assert exists(replica(rep).client, "admins/beto")
    assert rep.pending_count(primary(rep)) == 1

    revive(primary(rep))
    wait_recovery()
    admins.get("ana")
    assert exists(primary(rep).client, "admins/beto")


def test_config_transaction_survives_primary_loss(rep):
    config_store.save(AppConfig(), "ana", "config.update")
    kill(primary(rep))
    snap, _ = config_store.save(AppConfig(), "ana", "config.update")
    assert snap.version == 2                       # decided in the replica
    assert config_store.get_current().version == 2
    revive(primary(rep))
    wait_recovery()
    config_store.get_current()
    assert primary(rep).client.document("app_config/current").get().get("version") == 2
    assert exists(primary(rep).client, "app_config/current/versions/000002")


# ------------------------------------------- database recreated empty -- #
def test_recreated_empty_database_is_fully_resynced(rep):
    admins.create("ana", "contrasena-larga-1", "lector")
    config_store.save(AppConfig(), "ana", "config.update")
    kill(replica(rep))
    audit.record("ana", "admin.login")

    # same name, but empty: a different emulator database stands in for it
    fresh = firestore.Client(project=primary(rep).client.project, database="replica-recreated")
    revive(replica(rep), fresh)  # node keeps its name "replica", so the queue still targets it
    wait_recovery()
    admins.get("ana")
    for _ in range(50):  # resync runs in a background thread
        if not replica(rep).stale:
            break
        time.sleep(0.1)
    assert not replica(rep).stale
    for path in ("admins/ana", "app_config/current", "app_config/current/versions/000001"):
        assert exists(fresh, path), path
    assert len(list(fresh.collection("audit_log").stream())) == 1
    assert rep.pending_count(replica(rep)) == 0


# ------------------------------------------------------------- both die -- #
def test_both_databases_down_is_an_error(rep):
    kill(primary(rep))
    kill(replica(rep))
    with pytest.raises(gexc.NotFound):
        admins.create("ana", "contrasena-larga-1", "lector")


# ------------------------------------------------------------- endpoint -- #
def test_health_endpoint_reports_each_database(rep, client):
    kill(replica(rep))
    admins.create("ana", "contrasena-larga-1", "lector")
    r = client.get("/api/v1/health/databases")
    assert r.status_code == 200
    states = {d["name"]: d["state"] for d in r.json()["databases"]}
    assert states == {"primary": "activa", "replica": "caida"}
    assert r.json()["databases"][1]["pending"] == 1


def test_health_endpoint_503_when_no_database(rep, client):
    kill(primary(rep))
    kill(replica(rep))
    assert client.get("/api/v1/health/databases").status_code == 503
