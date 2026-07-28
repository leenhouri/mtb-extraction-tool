"""Pydantic models defining the Patient output contract -- the runtime schema that
constrains LLM generation and re-validates the extracted payload."""
from typing import Optional, List
from pydantic import BaseModel, Field


class Medication(BaseModel):
    medicationName: str
    dosage: str
    interval: str
    startDate: str
    endDate: Optional[str] = None


class Surgery(BaseModel):
    date: str
    type: str
    resectionStatus: str


class Treatment(BaseModel):
    type: str
    startDate: str
    endDate: Optional[str] = None
    treatmentLine: int
    status: str
    medications: Optional[List[Medication]] = None
    surgeries: Optional[List[Surgery]] = None


class TumorBoardMeetingOutcome(BaseModel):
    date: str
    input: str
    recommendation: str


class Biomarker(BaseModel):
    biomarker: str
    value: str
    type: str
    date: str


class Diagnosis(BaseModel):
    tumor: str
    date: str
    figoStage: Optional[str] = None
    tnmStage: Optional[str] = None
    resectionStatus: str
    relapse: str
    biomarkers: List[Biomarker] = Field(default_factory=list)


class Document(BaseModel):
    type: str
    date: str
    diagnoses: List[Diagnosis] = Field(default_factory=list)
    tumorBoardOutcomes: List[TumorBoardMeetingOutcome] = Field(default_factory=list)
    treatments: List[Treatment] = Field(default_factory=list)


class Patient(BaseModel):
    id: str
    dateOfBirth: str
    documents: List[Document] = Field(default_factory=list)