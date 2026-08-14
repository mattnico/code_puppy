from code_puppy.plugins.native_agents.integrations import kennel


def test_kennel_is_not_recalled_without_method_opt_in(monkeypatch):
    called = False

    def fake_recall():
        nonlocal called
        called = True
        return "memory"

    monkeypatch.setattr(kennel, "build_recall_block", fake_recall, raising=False)
    assert kennel.bounded_recall(enabled=False) is None
    assert called is False


def test_kennel_recall_is_bounded_and_fail_soft(monkeypatch):
    monkeypatch.setattr(kennel, "build_recall_block", lambda: "x" * 100)
    assert kennel.bounded_recall(enabled=True, max_chars=20) == "x" * 20
    monkeypatch.setattr(
        kennel, "build_recall_block", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    assert kennel.bounded_recall(enabled=True) is None
    assert kennel.curated_write(content="fact", explicit=True) is False
