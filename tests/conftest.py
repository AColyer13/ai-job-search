import pytest


@pytest.fixture(autouse=True)
def _agent_may_ask_adam_in_tests(monkeypatch):
    """The production default is no human in the loop (APPLY_ASK_ADAM unset); the ask flow is still tested, so tests turn it on."""
    monkeypatch.setenv("APPLY_ASK_ADAM", "1")
