from icart.application.ports import ModelContext, ToolCall


class ScriptedModel:
    """A test double, not natural-language understanding or an LLM benchmark."""

    def propose(self, context: ModelContext) -> ToolCall:
        return ToolCall("compare_baskets", context.available_offer_refs)
