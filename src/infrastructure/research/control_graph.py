"""Optional local LangGraph execution with automatic LangSmith tracing disabled."""

from typing import TypedDict

from src.application.strategy_control import StrategyControl
from src.domain.recommendation.contracts import RecommendationRequest
from src.domain.recommendation.control import ControlStep


class _State(TypedDict, total=False):
    request: RecommendationRequest
    options: dict
    step: ControlStep
    audit: dict


class LangGraphControl:
    """No checkpointer, daemon, cloud deployment or autonomous feedback loop."""

    def __init__(self, control: StrategyControl):
        from langgraph.graph import END, START, StateGraph

        self.control = control
        graph = StateGraph(_State)
        graph.add_node(
            "control", lambda state: {"step": control.run(state["request"], **state["options"])}
        )
        graph.add_node("audit", lambda state: {"audit": state["step"].audit()})
        graph.add_edge(START, "control")
        graph.add_edge("control", "audit")
        graph.add_edge("audit", END)
        self._graph = graph.compile()

    def run(self, request, **options):
        from langsmith import tracing_context

        # LANGSMITH_TRACING=true in a developer shell must not export graph state.
        with tracing_context(enabled=False):
            state = self._graph.invoke(
                {"request": request, "options": options},
                config={"callbacks": [], "recursion_limit": 5},
            )
        return state["step"]
