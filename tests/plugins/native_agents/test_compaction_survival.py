from code_puppy.plugins.native_agents.contracts import (
    ContextBlock,
    NativeContextView,
)
from code_puppy.plugins.native_agents.integrations.history import NativeHistoryContext


def test_follow_up_renders_fresh_durable_context_after_transcript_compaction():
    revision = {"value": 1}

    def render():
        return NativeContextView(
            execution_id="exec",
            blocks=[
                ContextBlock(
                    name="typed_state",
                    priority=80,
                    content=f"DATA revision={revision['value']}",
                    source="state",
                )
            ],
            total_chars=18,
        )

    history = NativeHistoryContext(render)
    first = history.before_native_follow_up()
    revision["value"] = 2
    second = history.before_native_follow_up()
    assert first.blocks[0].content == "DATA revision=1"
    assert second.blocks[0].content == "DATA revision=2"
