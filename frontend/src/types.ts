/** Mirrors the pydantic models the API serves.
 *
 *  Kept minimal and structural on purpose: these describe the wire shape, and the
 *  backend's models are the source of truth for what the fields mean. */

export type SpanStatus = "verified" | "normalized" | "absent" | "not_found";
export type Action = "proceed" | "review" | "stop";
export type Severity = "none" | "weak" | "possible" | "probable";
export type Outcome = "approved" | "approved_with_edits" | "rejected";

export interface Span {
  start: number;
  end: number;
  quote: string;
  status: SpanStatus;
  occurrences: number;
  /** The model gave no quote; the system located the value itself. */
  derived: boolean;
}

export interface TracedField<T = unknown> {
  value: T | null;
  confidence: number;
  self_reported: number | null;
  span: Span | null;
  validator: "passed" | "failed" | "not_applicable";
  validator_note: string | null;
  /** The arithmetic behind `confidence`, computed server-side. */
  explanation: string;
}

export interface Party {
  name: TracedField<string>;
  role: string;
  email: string | null;
  is_organization: boolean;
}

export interface Extraction {
  email_id: string;
  parties: Party[];
  matter_type: TracedField<string>;
  jurisdiction: TracedField<string>;
  key_dates: { label: string; value: TracedField<string> }[];
  amounts: { label: string; currency: string; value: TracedField<number> }[];
  summary: string;
  parse_warnings: string[];
}

export interface ConflictHit {
  rule_id: string;
  severity: Severity;
  inquiry_party: string;
  matched_record_kind: "client" | "matter";
  matched_record_id: string;
  matched_field: string;
  matched_value: string;
  score: number;
  explanation: string;
}

export interface Resolution {
  email_id: string;
  client_matches: {
    inquiry_party: string;
    inquiry_role: string;
    client_id: string;
    client_name: string;
    method: string;
    score: number;
  }[];
  matter_ids: string[];
  conflicts: ConflictHit[];
  checked_party_names: string[];
  completeness_notes: string[];
}

export interface Dispatch {
  email_id: string;
  attorney_id: string | null;
  routing_reason: string;
  acknowledgment: { subject: string; body: string; status: string } | null;
  matter_stub: {
    caption: string;
    practice_area: string;
    jurisdiction: string | null;
    prospective_client: string | null;
    opposing_parties: string[];
  } | null;
}

export interface Reason {
  code: string;
  message: string;
  /** Machine-readable: the UI scrolls to this. Never rendered. */
  field_path: string | null;
  /** The same field, in English. Rendered. */
  field_label: string;
  rule_id: string | null;
}

export interface StageTrace {
  email_id: string;
  stage: string;
  provider: string;
  model: string;
  cached: boolean;
  latency_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  raw_response: string;
  ok: boolean;
  failure_reason: string | null;
}

export interface FieldEdit {
  field_path: string;
  original_value: string | null;
  corrected_value: string | null;
  edited_by: string;
  edited_at: string;
}

export interface ReviewAction {
  email_id: string;
  outcome: Outcome;
  reviewer: string;
  note: string;
  edits: FieldEdit[];
  acted_at: string;
}

export interface PipelineResult {
  email_id: string;
  classification: { email_id: string; label: TracedField<string>; rationale: string } | null;
  extraction: Extraction | null;
  resolution: Resolution | null;
  dispatch: Dispatch | null;
  decision: { action: Action; reasons: Reason[] };
  traces: StageTrace[];
  failures: { stage: string; reason: string; raw_excerpt: string }[];
  review: ReviewAction | null;
}

export interface InboxItem {
  email_id: string;
  received_at: string;
  from_name: string;
  from_email: string;
  subject: string;
  preview: string;
  label: string | null;
  label_confidence: number | null;
  action: Action | null;
  reason_count: number;
  top_reason: string | null;
  conflict_severity: Severity | null;
  reviewed: boolean;
  attorney_id: string | null;
}

export interface Counts {
  total: number;
  proceed: number;
  review: number;
  stop: number;
  pending_review: number;
  reviewed: number;
}

export interface EmailDetail {
  email_id: string;
  received_at: string;
  from_name: string;
  from_email: string;
  to_address: string;
  subject: string;
  body: string;
  /** Spans index into THIS, not into body. */
  source_text: string;
  prose_source: string;
  result: PipelineResult | null;
  attorney: { id: string; name: string; email: string; practice_areas: string[]; capacity: number; current_load: number } | null;
  matched_clients: { client_id: string; client_name: string; method: string; score: number; inquiry_party: string; client: { id: string; display_name: string; domains: string[] } }[];
  review_log: ReviewAction[];
}

export interface Thresholds {
  classification: number;
  party_name: number;
  matter_type: number;
  conflict_floor: string;
}
