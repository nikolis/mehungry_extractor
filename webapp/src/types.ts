// Shapes returned by the /observe/* endpoints. Only the fields the UI reads are typed;
// everything is JSON from the Python engine, so unknown extras are allowed.

export interface DocumentSummary {
  document_id: string;
  pmid: string;
  pmcid: string | null;
  doi: string | null;
  title: string | null;
  source_type: string | null;
  publication_date: string | null;
}

export interface Sentence {
  sentence_id?: string;
  start_char: number;
  end_char: number;
  text: string;
}
export interface Paragraph {
  paragraph_id?: string;
  start_char: number;
  end_char: number;
  text: string;
  sentences: Sentence[];
}
export interface Section {
  section_id?: string;
  title?: string | null;
  section_type?: string | null;
  start_char: number;
  end_char: number;
  text: string;
  paragraphs: Paragraph[];
}
export interface Canonical {
  document_id: string;
  source_type: string | null;
  text: string;
  metadata: Record<string, unknown> & { title?: string | null; journal?: string | null };
  sections: Section[];
  summary: { sections: number; sentences: number; chars: number; source_type: string | null };
  paper_url: string;
}

export interface Modifier {
  relation: string;
  preposition: string;
  value_concept_id: string | null;
  value_text: string;
  value_type: string | null;
  status: string;
}
export interface Mention {
  mention_id: string;
  sentence_id: string | null;
  surface_text: string;
  normalized_text: string | null;
  concept_id: string | null;
  entity_type: string;
  start_char: number;
  end_char: number;
  status: string;
  normalization_source: string;
  modifiers: Modifier[];
}

export interface Qualifier {
  qualifier_type: string;
  value_concept_id: string | null;
  value_text: string | null;
}
export interface EvidenceRef {
  document_id?: string;
  section_id?: string | null;
  paragraph_id?: string | null;
  sentence_id?: string | null;
  start_char?: number | null;
  end_char?: number | null;
  quoted_text?: string | null;
  precision?: string;
  extraction_rule?: string;
  extraction_rule_version?: string;
}
export interface Observation {
  observation_id: string;
  sentence_id: string | null;
  // Phase 13 — the observation this one is nested beneath (its subject is that one's object), or null.
  parent_observation_id?: string | null;
  subject_text: string;
  subject_concept_id: string | null;
  subject_modifiers: Modifier[];
  predicate: string;
  object_text: string;
  object_concept_id: string | null;
  object_modifiers: Modifier[];
  polarity: string;
  certainty: string;
  context: string | null;
  rule_id: string;
  rule_version: string;
  qualifiers: Qualifier[];
  evidence_refs: EvidenceRef[];
}

export interface Claim {
  claim_id: string;
  // Phase 13 — the claim this one is nested beneath, or null for a top-level claim.
  parent_claim_id?: string | null;
  subject_concept_id: string;
  subject_name: string;
  predicate: string;
  object_concept_id: string;
  object_name: string;
  polarity: string;
  certainty: string;
  qualifiers: Qualifier[];
  evidence_count?: number;
  observation_ids?: string[];
  [k: string]: unknown;
}

export interface Facts {
  study_characteristics: Array<{
    field: string;
    value: string;
    classification_source: string;
    rule_id: string;
  }>;
  funding: Array<{ funder: string; funder_type: string; source: string }>;
  affiliations: Array<{
    name: string;
    position: number;
    affiliations: Array<{ raw_text: string; institution_name: string | null; institution_type: string | null }>;
  }>;
  assessments: Array<{
    framework_id: string;
    framework_version: string;
    criterion: string;
    value: string;
    rationale: string;
  }>;
}

export interface Provenance {
  claim: Record<string, unknown>;
  document: Record<string, unknown> | null;
  run: Record<string, unknown> | null;
  study: Record<string, unknown>;
  funding: Array<Record<string, unknown>>;
  assessments: Array<Record<string, unknown>>;
  observations: Array<Record<string, unknown>>;
  evidence: Array<EvidenceRef & { reconstructed_text?: string; reconstructs?: boolean }>;
}

export interface OpenRelation {
  open_observation_id: string;
  text: string;
  sentence_id: string | null;
  start_char: number;
  end_char: number;
  score: number;
  detector_name: string;
  detector_version: string;
}

// Batch synthesis
export interface Conclusion {
  subject_name: string;
  predicate: string;
  object_name: string;
  direction: string;
  clinical_direction: string;
  paper_count: number;
  supporting_papers: string[];
  contradicting_papers: string[];
  neutral_papers: string[];
  qualifiers: Qualifier[];
  evidence: Array<{ quoted_text: string; document_id: string }>;
}
export interface SynthesizeResult {
  report: {
    requested_pmids: string[];
    included_pmids: string[];
    papers: Array<Record<string, unknown>>;
    topic: { core_concepts: Array<{ concept_id: string; name: string | null; paper_count: number }> };
    outliers: Array<Record<string, unknown>>;
    warnings: string[];
  };
  synthesis: {
    conclusions: Conclusion[];
    derived_conclusions: Conclusion[];
    facts: Record<string, unknown>;
    warnings: string[];
  };
}

export interface StageRunResult {
  status: string;
  [k: string]: unknown;
}

// Sentence deconstruction (entities → observations sub-pipeline)
export interface ParseToken {
  i: number;
  text: string;
  lemma: string;
  pos: string;
  dep: string;
  head: number;
  start_char: number;
}
export interface ResolvedArg {
  surface: string;
  concept_id: string | null;
  status: string;
}
export interface PredicateHead {
  verb: string;
  lemma: string;
  dep: string;
  is_participial: boolean;
  negated: boolean;
  object_is_risk: boolean;
  object_direction: string | null;
  rule: { predicate: string; rule_id: string; version: string } | null;
  subjects: Array<ResolvedArg | null>;
  objects: Array<ResolvedArg | null>;
  conditions: string[];
  // Phase 13 — true when this predicate nests under the relation whose object is its subject.
  nested?: boolean;
  note: string;
}
export interface DeconClause {
  text: string;
  marker: string | null;
  contrastive: boolean;
  start_char: number;
  end_char: number;
}
export interface DeconSentence {
  sentence_id: string;
  text: string;
  start_char: number;
  end_char: number;
  mentions: Array<{ surface: string; concept_id: string | null; status: string; entity_type: string; start_char: number; end_char: number }>;
  clauses: DeconClause[];
  parse: { tokens: ParseToken[]; predicate_heads: PredicateHead[]; clausal_subjects: string[] } | null;
  observations: Array<{
    observation_id: string;
    // Phase 13 — the observation this one is nested beneath, or null if top-level.
    parent_observation_id: string | null;
    subject_text: string;
    subject_concept_id: string | null;
    predicate: string;
    object_text: string;
    object_concept_id: string | null;
    polarity: string;
    certainty: string;
    context: string | null;
    rule_id: string;
    // Clause-scoped typed conditions (e.g. a `disease_state`). A descriptive `characterized_by`
    // relation carries the whole descriptor phrase in `object_text`, not a qualifier (Phase 15).
    qualifiers: Qualifier[];
  }>;
}
export interface Deconstruction {
  document_id: string;
  pmid: string;
  parse_available: boolean;
  sentence_count: number;
  deconstructed_count: number;
  entities_present: boolean;
  observations_present: boolean;
  sentences: DeconSentence[];
}
