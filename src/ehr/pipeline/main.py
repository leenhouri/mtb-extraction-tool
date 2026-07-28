"""Batch entry point: run the extraction graph over every Markdown file in INPUT_DIR,
one checkpointed thread per file."""
from dotenv import load_dotenv
load_dotenv()

import os
import glob
import uuid
import re
import structlog
from langgraph.checkpoint.sqlite import SqliteSaver
from ehr.pipeline.graph import create_pipeline_graph
from ehr.pipeline.state import GraphState
from langchain_core.runnables import RunnableConfig

# Configure structlog for verbose JSON logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)
logger = structlog.get_logger()


def patient_id_from_filename(path: str) -> str:
    """Extract the leading numeric patient ID from a filename."""
    name = os.path.basename(path)
    m = re.match(r'(\d+)', name)
    return m.group(1) if m else os.path.splitext(name)[0]


def main():
    input_dir = os.getenv("INPUT_DIR", ".")
    db_path = os.getenv("DB_PATH", "checkpoints.sqlite")

    workflow = create_pipeline_graph()
    logger.info("pipeline_started", input_dir=input_dir)

    with SqliteSaver.from_conn_string(db_path) as checkpointer:
        checkpointer.setup()
        app = workflow.compile(checkpointer=checkpointer)

        # All .md files directly inside input_dir, one per patient
        md_files = sorted(glob.glob(os.path.join(input_dir, "*.md")))

        for file_path in md_files:
            doc_id = os.path.abspath(file_path)
            patient_id = patient_id_from_filename(file_path)
            thread_id = str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))
            logger.info("processing_document", document_id=doc_id, thread_id=thread_id, patient_id=patient_id)
            config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
            initial_state: GraphState = {
                "document_id": doc_id,
                "raw_text": "",
                "extracted_data": None,
                "validation_errors": [],
                "iteration_count": 0,
                "status": "loading"
            }
            try:
                final_state = app.invoke(initial_state, config=config)
                logger.info("document_processed", document_id=doc_id, final_status=final_state.get("status"))
            except Exception as e:
                logger.error("pipeline_crashed", error=str(e), document_id=doc_id)


if __name__ == "__main__":
    main()