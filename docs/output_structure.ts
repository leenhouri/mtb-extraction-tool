/**
 * Target output structure for the EHR extraction pipeline (human reference).
 *
 * This file documents the shape the pipeline produces for a single Patient.
 * It is NOT imported at runtime — the executable contract is the Pydantic model
 * in `schema.py`, and the allowed enum values are defined in the extraction
 * prompt (`prompt.py`). Keep this file in sync with both.
 *
 * Conventions:
 *  - Dates are ISO `YYYY-MM-DD` strings.
 *  - Optional fields (marked `?`) are omitted / null when not present.
 *  - Required free-text string fields are set to the literal "Not documented" when
 *    the value is missing or unclear; enum fields use their "Unclear" member when
 *    the document addresses the field but the value is genuinely indeterminate.
 *  - The string-literal unions below mirror the values enforced by the prompt.
 *    `schema.py` types these as plain strings, so they are not enforced at the
 *    type level — these unions are documentation of the intended value set.
 */

interface Patient {
  id: string;
  dateOfBirth: string; // ISO Date
  documents: Document[];
}

interface Document {
  type: "Physician Letter" | "Tumor Board Meeting Protocol";
  date: string;
  diagnoses: Diagnosis[];
  tumorBoardOutcomes: TumorBoardMeetingOutcome[];
  treatments: Treatment[];
}

interface Diagnosis {
  tumor: string;
  date: string;
  figoStage?: string;
  tnmStage?: string;
  resectionStatus: "R0" | "Complete" | "Partial" | "R1" | "R2" | "Unclear" | "No Surgery";
  relapse: "Yes" | "No" | "Unclear";
  biomarkers: Biomarker[];
}

interface Biomarker {
  biomarker: string;
  value: string; // qualitative ("positive", "mutated", "wildtype") or quantitative (">90%", "45%")
  type: "Germline" | "Somatic" | "Unclear";
  date: string;
}

interface TumorBoardMeetingOutcome {
  date: string;
  input: string;
  recommendation: string;
}

interface Treatment {
  type: "Surgery" | "Systemic Treatment" | "Maintenance Treatment";
  startDate: string;
  endDate?: string;
  treatmentLine: number;
  status: "Completed" | "Aborted" | "Unclear";
  // Modality-specific details depending on the treatment type
  medications?: Medication[];
  surgeries?: Surgery[];
}

interface Medication {
  medicationName: string;
  dosage: string;
  interval: string;
  startDate: string;
  endDate?: string;
}

interface Surgery {
  date: string;
  type: "Längslaparotomie" | "Laparoskopie" | "andere Operation";
  resectionStatus: "R0" | "Complete" | "Partial" | "R1" | "R2" | "Unclear";
}