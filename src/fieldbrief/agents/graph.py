"""Builds the compiled LangGraph (ADR 0010): plan -> research -> verify (inside
research) -> gap_check -> [loop back to research, or advance] -> analyze ->
synthesize -> deliver.

Stage 3 scope: single company, no competitors, no checkpointer — the outer
per-company fan-out and Postgres checkpointing (resumability) are stage 4/6
additions, not needed for one company run synchronously from the CLI.
"""

from functools import partial

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents.nodes import (
    analyze_node,
    deliver_node,
    gap_check_node,
    plan_node,
    research_node,
    should_continue_researching,
    synthesize_node,
)
from fieldbrief.agents.state import GraphState
from fieldbrief.fetch.http import Fetcher
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway


def build_graph(
    *,
    session: AsyncSession,
    gateway: LLMGateway,
    budget: Budget,
    fetcher: Fetcher,
    http_client: httpx.AsyncClient,
    searxng_url: str,
    tier: str,
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
        ),
    )
    graph.add_node("gap_check", partial(gap_check_node, session=session))
    graph.add_node("analyze", partial(analyze_node, session=session))
    graph.add_node(
        "synthesize", partial(synthesize_node, session=session, gateway=gateway, budget=budget)
    )
    graph.add_node("deliver", partial(deliver_node, session=session, budget=budget))

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "research")
    graph.add_edge("research", "gap_check")
    graph.add_conditional_edges(
        "gap_check",
        partial(should_continue_researching, tier=tier),
        {"loop": "research", "advance": "analyze"},
    )
    graph.add_edge("analyze", "synthesize")
    graph.add_edge("synthesize", "deliver")
    graph.add_edge("deliver", END)

    return graph.compile()
