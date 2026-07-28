"""GraphState: the typed execution context passed between LangGraph nodes."""
from typing import Any, Dict, Optional, Literal
from typing_extensions import TypedDict

class GraphState(TypedDict):
    document_id: str
    raw_text: str
    extracted_data: Optional[Dict[str, Any]]
    validation_errors: list[str]
    iteration_count: int
    status: Literal["loading", "extracting", "validating", "persisting", "failed", "completed"]
