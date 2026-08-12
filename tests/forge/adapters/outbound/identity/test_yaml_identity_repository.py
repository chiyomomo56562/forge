from forge.adapters.outbound.identity import YamlIdentityRepository


def test_loads_identity_and_known_capability():
    repository = YamlIdentityRepository("identity")

    identity = repository.load_identity()
    coding = repository.capability_for("coding")

    assert identity.name == "Gnosis"
    assert identity.autonomy_level == "L1"
    assert coding.category == "coding"
    assert coding.confidence == 0.7


def test_uses_configured_defaults_for_unknown_capability():
    capability = YamlIdentityRepository("identity").capability_for("novel_task")

    assert capability.category == "novel_task"
    assert capability.success_rate == 0.5
    assert capability.total_attempts == 0
