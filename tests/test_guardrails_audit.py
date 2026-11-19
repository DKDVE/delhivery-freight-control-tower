"""AUDIT_LOG_DIR: one append-only file per replica, merged on read."""
from app import guardrails as G


def test_audit_path_is_named_by_replica(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HOSTNAME", "replica-a")
    assert G.audit_path() == str(tmp_path / "audit-replica-a.jsonl")


def test_two_replicas_write_separate_files_and_reads_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))

    monkeypatch.setenv("HOSTNAME", "replica-a")
    id_a = G.log_run("Booking check", {}, [], "rules", "text a", [], "DEMO")

    monkeypatch.setenv("HOSTNAME", "replica-b")
    id_b = G.log_run("Booking check", {}, [], "rules", "text b", [], "DEMO")

    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["audit-replica-a.jsonl", "audit-replica-b.jsonl"]

    run_ids = {r["run_id"] for r in G.read_audit()}
    assert run_ids == {id_a, id_b}


def test_read_audit_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path / "does_not_exist"))
    assert G.read_audit() == []
