"""
Pipeline node functions for the insurance contract extraction pipeline.

Each node is a pure function (PipelineState, gemini) -> dict that returns
a partial state update. This matches the LangGraph node contract:
  graph.add_node("extract", lambda state: extraction_node(state, gemini))

To adopt LangGraph, replace run_pipeline() with a StateGraph assembly.
"""

from loguru import logger as log
from models import FinalContract, PipelineState
from extractor import extract_all_documents
from aggregator import aggregate
from validator import validate
from refiner import refine


def extraction_node(state: PipelineState, gemini) -> dict:
    extracts = extract_all_documents(state["documents"], gemini)
    return {"extracts": extracts}


def aggregation_node(state: PipelineState) -> dict:
    aggregated = aggregate(state["extracts"])
    return {"aggregated": aggregated}


def refinement_node(state: PipelineState, gemini) -> dict:
    refined = refine(state["aggregated"], state["extracts"], gemini, documents=state["documents"])
    return {"refined": refined}


def validation_node(state: PipelineState, gemini) -> dict:
    validated = validate(state["documents"], state["extracts"], state["refined"], gemini)
    return {"validated": validated}


def run_pipeline(documents: list[dict], gemini_extract, gemini_validate, gemini_refine) -> FinalContract:
    state: PipelineState = {"documents": documents}
    state.update(extraction_node(state, gemini_extract))
    log.debug(f"Extracted {len(state['extracts'])} documents")
    state.update(aggregation_node(state))
    log.debug(f"Aggregated contract: {state['aggregated']}")
    state.update(refinement_node(state, gemini_refine))
    log.debug(f"Refined contract: {state['refined']}")
    #state.update(validation_node(state, gemini_validate))
    #log.debug(f"Validated contract: {state['validated']}")
    return state["refined"]
