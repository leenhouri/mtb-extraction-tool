"""Graph node implementations: load the document, call the LLM for structured
extraction, validate (schema + domain logic), and persist the per-patient JSON."""
import os
import re
import json
import time
import structlog
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr
from ehr.pipeline.prompt import (
    SYSTEM_PROMPT,
    USER_PROMPT_BASE,
    VALIDATION_ERROR_TEMPLATE,
)

from ehr.pipeline.state import GraphState
from ehr.pipeline.schema import Patient

logger = structlog.get_logger()

# Tokens that mean "no value" (LLM2 emits "Not documented"; older runs used "unknown").
_EMPTY = (None, "", "unknown", "Not documented")


def patient_id_from_path(document_id: str) -> str:
    """Extract leading numeric patient ID from a filename path."""
    name = os.path.basename(document_id)
    m = re.match(r'(\d+)', name)
    return m.group(1) if m else os.path.splitext(name)[0]


def _build_chat_llm():
    """LangChain ChatOpenAI client (used by agent_extraction_node)."""
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-oss"),
        temperature=0.0,
        api_key=SecretStr(os.getenv("LLM_API_KEY", "no-key-required")),
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:8000/v1"),
        timeout=300,
    )


# Maximum retries for validation loop
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))


def load_document_node(state: GraphState) -> dict:
    """Reads the raw markdown content into state."""
    file_path = state["document_id"]
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
        return {
            "raw_text": raw_text,
            "status": "extracting"
        }
    except Exception as e:
        logger.error("load_failed", error=str(e), document_id=state["document_id"])
        return {
            "validation_errors": [f"File read error: {str(e)}"],
            "status": "failed"
        }


def agent_extraction_node(state: GraphState) -> dict:
    """Invokes the LLM to extract structured data from raw text."""
    logger.info("extracting_data", iteration=state.get("iteration_count", 0), document_id=state["document_id"])

    llm = _build_chat_llm()
    structured_llm = llm.with_structured_output(Patient, method="json_schema", include_raw=True)

    user_prompt = USER_PROMPT_BASE.format(document_text=state['raw_text'])

    # Append Pydantic validation errors if present
    if state.get("validation_errors"):
        errors_str = "\n".join(state["validation_errors"])
        user_prompt += VALIDATION_ERROR_TEMPLATE.format(errors_str=errors_str)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt)
    ]

    logger.debug("user_prompt_constructed", length=len(user_prompt),
                 has_validation_errors=bool(state.get("validation_errors")))

    try:
        start_time = time.time()
        response = structured_llm.invoke(messages)
        latency = time.time() - start_time

        extracted_patient: Patient | None = response.get("parsed")
        raw_msg = response.get("raw")

        if raw_msg and hasattr(raw_msg, "usage_metadata") and raw_msg.usage_metadata:
            usage = raw_msg.usage_metadata
            logger.info(
                "llm_telemetry",
                document_id=state["document_id"],
                latency_seconds=round(latency, 2),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                reasoning_tokens=usage.get("output_token_details", {}).get("reasoning", 0) if isinstance(usage.get("output_token_details"), dict) else 0
            )

        if extracted_patient is None:
            raise ValueError("Structured output parser returned None.")

        data = extracted_patient.model_dump()
        data["id"] = patient_id_from_path(state["document_id"])
        logger.info("id_set", patient_id=data["id"], doc=state["document_id"])

        return {
            "extracted_data": data,
            "status": "validating",
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
    except Exception as e:
        logger.error("extraction_failed", error=str(e), document_id=state["document_id"])
        return {
            "validation_errors": [f"LLM Extraction failed: {str(e)}"],
            "status": "extracting",
            "iteration_count": state.get("iteration_count", 0) + 1,
        }


def validation_node(state: GraphState) -> dict:
    """Validates structural and domain-specific logic against the returned payload."""
    logger.info("validating_data", document_id=state["document_id"])

    data = state.get("extracted_data")
    if not data:
        return {"validation_errors": ["No extracted data found."], "status": "extracting"}

    errors = []

    try:
        patient_obj = Patient(**data)

        # For each treatment, if startDate and endDate are both real dates, enforce startDate <= endDate.
        for doc_idx, doc in enumerate(patient_obj.documents):
            for tr_idx, treatment in enumerate(doc.treatments):
                if treatment.startDate not in _EMPTY and treatment.endDate not in _EMPTY:
                    try:
                        if treatment.startDate > treatment.endDate:
                            errors.append(f"Document {doc_idx}, Treatment {tr_idx}: startDate ({treatment.startDate}) is after endDate ({treatment.endDate}).")
                    except Exception:
                        pass
    except Exception as e:
        errors.append(f"Schema validation error: {str(e)}")

    if errors:
        logger.warn("validation_failed", errors=errors, document_id=state["document_id"])
        return {"validation_errors": errors, "status": "extracting"}

    logger.info("validation_passed", document_id=state["document_id"])
    return {"status": "persisting"}


def persist_json_node(state: GraphState) -> dict:
    """Merges each document into one per-patient JSON: <patient_id>.json."""
    logger.info("persisting_patient_json", document_id=state["document_id"])

    data = state.get("extracted_data")
    if not data:
        return {"status": "failed", "validation_errors": ["No data to persist."]}

    try:
        output_dir = os.getenv("OUTPUT_DIR", "./output")
        os.makedirs(output_dir, exist_ok=True)

        patient_id = patient_id_from_path(state["document_id"])
        json_path = os.path.join(output_dir, f"{patient_id}.json")

        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                patient = json.load(f)
            patient.setdefault("documents", [])
            patient["documents"].extend(data.get("documents", []))
            if patient.get("id") in _EMPTY and data.get("id"):
                patient["id"] = data["id"]
            if patient.get("dateOfBirth") in _EMPTY and data.get("dateOfBirth"):
                patient["dateOfBirth"] = data["dateOfBirth"]
        else:
            patient = data

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(patient, f, indent=2, ensure_ascii=False)

        logger.info("persisted_successfully", json_path=json_path)
        return {"status": "completed"}
    except Exception as e:
        logger.error("persistence_failed", error=str(e), document_id=state["document_id"])
        return {"status": "failed", "validation_errors": [f"Failed to write JSON: {str(e)}"]}