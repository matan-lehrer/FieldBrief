from functools import partial

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents.field_checklist import Tier
from fieldbrief.agents.nodes import (
    analyze_node,
    gap_check_node,
    plan_node,
    research_node,
    should_continue_researching,
)
from fieldbrief.agents.progress import NullProgressSink, ProgressSink
from fieldbrief.agents.state import GraphState
from fieldbrief.fetch.http import Fetcher
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway

_NULL_PROGRESS = NullProgressSink()


def build_company_research_graph(
    *,
    session: AsyncSession,
    gateway: LLMGateway,
    budget: Budget,
    fetcher: Fetcher,
    http_client: httpx.AsyncClient,
    searxng_url: str,
    tier: Tier,
    edgar_client: httpx.AsyncClient | None = None,
    progress: ProgressSink = _NULL_PROGRESS,
    deadline: float | None = None,
) -> CompiledStateGraph[GraphState, None, GraphState, GraphState]:
    graph = StateGraph(GraphState)

    graph.add_node("plan", partial(plan_node, session=session))
    graph.add_node(
        "research",
        partial(
            research_node,
            session=session,
            gateway=gateway,
            budget=budget,
            fetcher=fetcher,
            http_client=http_client,
            searxng_url=searxng_url,
            edgar_client=edgar_client,
            progress=progress,
        ),
    )
    graph.add_node("gap_check", partial(gap_check_node, session=session, tier=tier))
    graph.add_node("analyze", partial(analyze_node, session=session))

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "research")
    graph.add_edge("research", "gap_check")
    graph.add_conditional_edges(
        "gap_check",
        partial(should_continue_researching, tier=tier, deadline=deadline),
        {"loop": "research", "advance": "analyze"},
    )
    graph.add_edge("analyze", END)

    return graph.compile()
