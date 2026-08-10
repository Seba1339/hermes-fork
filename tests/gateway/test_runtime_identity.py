from gateway import runtime_identity


def test_runtime_identity_has_stable_structure_fields():
    identity = runtime_identity.get_runtime_identity()

    assert identity["structure_id"] == "personal-system-memory"
    assert identity["structure_version"] == 1
    assert identity["integration_commit"] == "5a3d86abdf99238607a3ff1037d97bb3a29fb4f0"
    assert identity["channel"] == "integration"
    assert "package_version" in identity
    assert "source_version" in identity


def test_runtime_identity_tolerates_missing_package_metadata(monkeypatch):
    monkeypatch.setattr(runtime_identity, "get_package_version", lambda: None)

    identity = runtime_identity.get_runtime_identity()

    assert identity["package_version"] is None
    assert identity["structure_id"] == "personal-system-memory"
