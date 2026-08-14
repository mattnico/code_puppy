from code_puppy.plugins.native_agents.integrations import sessions


def test_session_boundary_correlates_only_string_ids():
    class Agent:
        session_id = "session-1"

    assert sessions.session_id(Agent()) == "session-1"
    assert sessions.restore_policy() == {
        "inspect_execution_metadata": True,
        "resume_native_execution": False,
    }


def test_missing_session_id_is_optional():
    assert sessions.session_id(object()) is None
