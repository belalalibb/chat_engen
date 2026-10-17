def test_snapshot_zero_limits_are_unknown() -> None:
    """models.dev emits ``context: 0`` for some entries; CanonicalModel requires >= 1."""
    from chat_engine.catalog.service import _i

    assert _i(0) is None and _i(-5) is None and _i(True) is None and _i(128000) == 128000
