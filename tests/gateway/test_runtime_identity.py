from gateway import runtime_identity


def test_runtime_identity_has_stable_structure_fields():
    identity = runtime_identity.get_runtime_identity()

    assert identity["structure_id"] == "personal-system-memory"
    assert identity["structure_version"] == 1
    assert identity["integration_commit"] == "a57f4b674c4b0d4d34ee6e06b214f61364f6d20e"
    assert identity["channel"] == "integration"
    assert "package_version" in identity
    assert "source_version" in identity


def test_runtime_identity_tolerates_missing_package_metadata(monkeypatch):
    monkeypatch.setattr(runtime_identity, "get_package_version", lambda: None)

    identity = runtime_identity.get_runtime_identity()

    assert identity["package_version"] is None
    assert identity["structure_id"] == "personal-system-memory"
