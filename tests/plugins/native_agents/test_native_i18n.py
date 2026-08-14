from code_puppy import i18n


def test_native_user_messages_have_catalog_entries():
    assert i18n.t("native_agents.command.status", enabled="on", codeact="off") == (
        "Native agents: on; CodeAct: off."
    )
    assert "native_agents.command" not in i18n.t(
        "native_agents.command.diagnostics_disabled"
    )
