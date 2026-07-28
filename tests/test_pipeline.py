"""Deterministic pipeline behavior: routing, id parsing, schema, persist-merge.

The LLM extraction call itself is not exercised (it needs a live endpoint); these
cover the parts that run without one."""
import json
from typing import cast

from ehr.pipeline import graph, nodes, schema
from ehr.pipeline.state import GraphState


def _gs(**overrides) -> GraphState:
    """A full GraphState with the given fields overridden (keeps the type checker happy)."""
    state = {"document_id": "/x/0001_a.md", "raw_text": "", "extracted_data": None,
             "validation_errors": [], "iteration_count": 0, "status": "loading"}
    state.update(overrides)
    return cast(GraphState, state)


def test_graph_routing():
    assert graph.route_load(_gs(status="failed")) == "failed"
    assert graph.route_load(_gs(status="loading")) == "extract"
    assert graph.route_extract(_gs(status="failed")) == "failed"
    assert graph.route_extract(_gs(status="validating")) == "validate"
    assert graph.route_extract(_gs(status="extracting", iteration_count=99)) == "failed"
    assert graph.route_validate(_gs(status="persisting")) == "persist"
    assert graph.route_validate(_gs(status="extracting", iteration_count=0)) == "extract"
    assert graph.route_validate(_gs(status="extracting", iteration_count=99)) == "failed"


def test_patient_id_parsing():
    assert nodes.patient_id_from_path("/data/0042_tumorboard.md") == "0042"
    assert nodes.patient_id_from_path("C:\\x\\0007.json") == "0007"


def test_schema_round_trip():
    data = {"id": "0001", "dateOfBirth": "1958-03-12",
            "documents": [{"type": "Tumor Board Meeting Protocol", "date": "2019-05-20",
                           "diagnoses": [{"tumor": "x", "date": "2018-11-02", "resectionStatus": "R0",
                                          "relapse": "No", "biomarkers": []}],
                           "tumorBoardOutcomes": [], "treatments": []}]}
    p = schema.Patient(**data)
    assert p.id == "0001"
    assert len(p.documents) == 1


def test_persist_merges_documents_per_patient(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))

    def state(suffix, doc_type):
        doc = {"type": doc_type, "date": "2019-01-01",
               "diagnoses": [], "tumorBoardOutcomes": [], "treatments": []}
        return _gs(document_id=f"/x/0001_{suffix}.md",
                   extracted_data={"id": "0001", "dateOfBirth": "1958-03-12", "documents": [doc]})

    assert nodes.persist_json_node(state("a", "Physician Letter"))["status"] == "completed"
    assert nodes.persist_json_node(state("b", "Tumor Board Meeting Protocol"))["status"] == "completed"

    data = json.loads((tmp_path / "0001.json").read_text(encoding="utf-8"))
    assert data["id"] == "0001"
    assert len(data["documents"]) == 2
