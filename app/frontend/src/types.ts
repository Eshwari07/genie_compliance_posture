export type Cell = string | number | boolean | null;

export interface Column {
  name: string;
  type: string;
}

export interface GenieAnswer {
  conversation_id: string;
  message_id: string;
  question: string;
  text: string | null;
  sql: string | null;
  columns: Column[];
  rows: Record<string, Cell>[];
  row_count: number;
  truncated: boolean;
  elapsed_s: number;
  error: string | null;
}

/** Events streamed from POST /api/ask. */
export type StreamEvent =
  | { type: "status"; stage: string; label: string; elapsed_s: number }
  | { type: "sql"; sql: string }
  | { type: "done"; answer: GenieAnswer }
  | { type: "error"; message: string; elapsed_s?: number };

export interface Posture {
  coverage_pct: number | null;
  total_obligations: number | null;
  covered: number | null;
  partial: number | null;
  gaps: number | null;
  high_criticality_gaps: number | null;
  sql: string | null;
  narrative: string | null;
  conversation_id: string | null;
  elapsed_s: number | null;
  source: string;
  cached: boolean;
}

export interface SuggestionGroup {
  id: string;
  label: string;
  hint: string;
  questions: string[];
}

export interface Suggestions {
  groups: SuggestionGroup[];
  tiles: Record<string, string>;
}

export interface ObligationDetail {
  obligation_id: string;
  framework: string;
  control_ref: string;
  obligation_title: string;
  requirement_text: string;
  domain: string;
  criticality: string;
  text_provenance: string;
  coverage_status: string;
  gap_reason: string | null;
  assessment_confidence: number | null;
  assessment_method: string;
  human_reviewed: boolean | string;
  unified_control_id: string;
  unified_control_name: string;
  csf_function: string;
  control_id: string | null;
  implementation_status: string | null;
  control_owner: string | null;
  control_owner_team: string | null;
  control_last_tested_date: string | null;
  control_is_untested: boolean | string | null;
  policy_doc_number: string | null;
  policy_title: string | null;
  policy_clause_ref: string | null;
  policy_section_heading: string | null;
  evidence_text: string | null;
  evidence_page_no: number | string | null;
  policy_last_reviewed_date: string | null;
  policy_is_stale: boolean | string | null;
}

export interface Sibling {
  framework: string;
  control_ref: string;
  obligation_title: string;
  criticality: string;
  coverage_status: string;
  overlap_type: string;
}

export interface Evidence {
  obligation: ObligationDetail;
  also_satisfies: Sibling[];
}

/** One turn in the conversation, including its in-flight state. */
export interface Turn {
  id: string;
  question: string;
  status: string;
  statusLabel: string;
  elapsed: number;
  sql: string | null;
  answer: GenieAnswer | null;
  error: string | null;
  done: boolean;
}
