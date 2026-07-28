"""LangGraph workflow definition and conditional routing
(load -> extract -> validate -> persist, with a bounded self-correction loop)."""
from langgraph.graph import StateGraph, START, END

from ehr.pipeline.state import GraphState
from ehr.pipeline.nodes import (
    load_document_node,
    agent_extraction_node,
    validation_node,
    persist_json_node,
    MAX_RETRIES,
)


def route_validate(state: GraphState) -> str:
    """Conditional edge from validation."""
    if state["status"] == "extracting":
        if state.get("iteration_count", 0) < MAX_RETRIES:
            return "extract"
        return "failed"
    elif state["status"] == "persisting":
        return "persist"
    return "failed"


def route_load(state: GraphState) -> str:
    """Conditional edge from loading."""
    if state["status"] == "failed":
        return "failed"
    return "extract"


def route_extract(state: GraphState) -> str:
    """Conditional edge from extraction."""
    if state["status"] == "failed":
        return "failed"
    elif state["status"] == "extracting":
        if state.get("iteration_count", 0) < MAX_RETRIES:
            return "extract"
        return "failed"
    return "validate"


def create_pipeline_graph():
    workflow = StateGraph(GraphState)

    workflow.add_node("load", load_document_node)
    workflow.add_node("extract", agent_extraction_node)
    workflow.add_node("validate", validation_node)
    workflow.add_node("persist", persist_json_node)
    workflow.add_node("failed_state", lambda state: {"status": "failed"})

    workflow.add_edge(START, "load")

    workflow.add_conditional_edges(
        "load",
        route_load,
        {"extract": "extract", "failed": "failed_state"},
    )

    workflow.add_conditional_edges(
        "extract",
        route_extract,
        {"extract": "extract", "validate": "validate", "failed": "failed_state"},
    )

    workflow.add_conditional_edges(
        "validate",
        route_validate,
        {
            "extract": "extract",
            "persist": "persist",
            "failed": "failed_state",
        },
    )

    workflow.add_conditional_edges(
        "persist",
        lambda state: "failed" if state.get("status") == "failed" else "end",
        {"failed": "failed_state", "end": END},  
    )
    workflow.add_edge("failed_state", END)

    return workflow
