// Types mirror backend API response shapes (docs/10_data_model.md,
// docs/11_api_contracts.md). Only the fields actually rendered by the
// frontend are declared; unknown/unused fields from the backend are
// ignored rather than given fabricated shapes.

export type ApiError = {
  code: string;
  field: string | null;
  message: string;
};

export type ApiResponse<T> = {
  success: boolean;
  data: T | null;
  message: string | null;
  errors: ApiError[];
};

export type TripRequestInput = {
  destination_scope: "single_city";
  primary_destination: string;
  origin_city: string;
  start_date: string;
  end_date: string;
  travelers_count: number;
  travel_group_type: "solo" | "couple" | "family" | "friends" | "group";
  pace: "relaxed" | "balanced" | "packed";
  budget_min?: number;
  budget_max?: number;
  interests?: string[];
  must_visit?: string[];
  constraints?: string[];
  free_text_preferences?: string;
};

export type TripCreateData = {
  trip_id: string;
};

export type ProviderCoverage = Record<string, string | null>;

export type TripSummary = {
  trip_id: string;
  primary_destination: string;
  start_date: string;
  end_date: string;
  pipeline_status: string;
  active_stage: string | null;
  provider_coverage: ProviderCoverage;
  destination_context_generated: boolean;
  experience_plan_generated: boolean;
  validation_report_generated: boolean;
  candidate_pois_count: number;
  candidate_restaurants_count: number;
  candidate_accommodation_pois_count: number;
  scheduled_experiences_count: number;
  validation_status: string | null;
  main_blocking_reason: string | null;
  main_review_reason: string | null;
};

export type GeoPoint = {
  lat: number;
  lng: number;
};

export type CandidatePoi = {
  place_id: string;
  name: string;
  category: string | null;
  coordinates: GeoPoint | null;
  address: string | null;
  source: string;
  data_status: string;
  confidence: number;
};

export type DestinationContextData = {
  trip_id: string;
  destination_context: {
    destination_name: string;
    candidate_pois: CandidatePoi[];
    candidate_restaurants: CandidatePoi[];
    candidate_accommodation_pois: CandidatePoi[];
    assumptions: string[];
    confidence: number;
  };
  weather_context: WeatherContext | null;
  holiday_context: HolidayContext | null;
  currency_context: CurrencyContext | null;
};

export type DailyWeather = {
  date: string;
  temperature_max_c: number | null;
  temperature_min_c: number | null;
  precipitation_probability_max: number | null;
  precipitation_sum_mm: number | null;
  weather_code: number | null;
  source: string;
  data_status: string;
};

export type WeatherContext = {
  destination: string;
  start_date: string;
  end_date: string;
  daily_weather: DailyWeather[];
  source: string | null;
  data_status: string;
  confidence: number;
  assumptions: string[];
  warnings: string[];
};

export type Holiday = {
  date: string;
  local_name: string;
  name: string;
  country_code: string;
  is_global: boolean;
  counties: string[];
  types: string[];
  source: string;
  data_status: string;
};

export type HolidayContext = {
  destination: string;
  start_date: string;
  end_date: string;
  country_code: string | null;
  holidays: Holiday[];
  source: string | null;
  data_status: string;
  confidence: number;
  assumptions: string[];
  warnings: string[];
};

export type CurrencyContext = {
  base_currency: string;
  destination_currency: string | null;
  exchange_rate: number | null;
  rate_date: string | null;
  source: string | null;
  data_status: string;
  confidence: number;
  assumptions: string[];
  warnings: string[];
};

// `promoted_from_ai`/`original_ai_candidate_id`/`provider_place_id`/
// `provider_source` (Step 170D, backend: app.models.planning_state.
// ExperienceItem) are set only for an experience that came from an
// already-promoted AI candidate (Step 170C) -- `false`/`null` for a
// normally-scheduled real provider candidate. They never carry a rating,
// opening hour, price, route, or booking link; they only ever restate
// identifiers already present on the underlying PromotedAICandidate.
export type ExperienceItem = {
  experience_id: string;
  name: string;
  category: string;
  coordinates: GeoPoint | null;
  start_time: string | null;
  end_time: string | null;
  estimated_duration_minutes: number | null;
  why_included: string | null;
  confidence: number;
  promoted_from_ai: boolean;
  original_ai_candidate_id: string | null;
  provider_place_id: string | null;
  provider_source: string | null;
};

export type RestaurantSuggestion = {
  name: string;
  category: string | null;
  coordinates: GeoPoint | null;
  address: string | null;
  source: string | null;
  data_status: string;
  confidence: number;
  why_suggested: string;
};

export type AccommodationSuggestion = {
  name: string;
  category: string | null;
  coordinates: GeoPoint | null;
  address: string | null;
  source: string | null;
  data_status: string;
  confidence: number;
  why_suggested: string;
};

// Provenance for one scraped (not official-provider) accommodation offer
// (backend: app.models.scraping.ScrapedDataProvenance, Step 168B/168E).
// `official_provider` is always `false` here -- the backend's own type
// (`Literal[False]`) makes any other value impossible to serialize.
// Present on an `AccommodationOffer` only when that offer came from
// `ScrapedAccommodationProvider` (Step 168C), never from an official
// lodging provider.
export type ScrapedAccommodationProvenance = {
  source_id: string;
  source_name: string;
  source_type: string;
  provenance: string;
  confidence: "experimental" | "fragile";
  fetched_at: string | null;
  parser_version: string | null;
  source_url: string | null;
  extraction_method: string;
  official_provider: false;
};

// Bookable accommodation inventory offer (backend:
// app.models.accommodation.AccommodationOffer). This is a wholly separate
// concept from `AccommodationSuggestion`/`CandidatePoi` above (open-data
// OSM location candidates) -- those never carry a price, availability,
// rating, amenity, or booking link, and this is the only place one could
// ever legitimately appear. Every optional field here stays `null`/empty
// unless a real provider actually returned it; the frontend never fills
// one in. `scraped_provenance` (Step 168E) is set only when this offer
// came from the scraped/experimental path (`ScrapedAccommodationProvider`,
// disabled by default) rather than an official, connected lodging
// provider -- when present, this offer must be labeled as scraped/
// experimental/fragile and never presented as official-provider data.
export type AccommodationOffer = {
  provider: string;
  provider_property_id: string;
  property_name: string;
  nightly_price_amount: number | null;
  total_price_amount: number | null;
  currency: string | null;
  availability_status: string;
  booking_url: string | null;
  rating: number | null;
  amenities: string[];
  cancellation_policy: string | null;
  source_name: string | null;
  data_status: string;
  scraped_provenance: ScrapedAccommodationProvenance | null;
};

// Bookable accommodation inventory report for the whole trip (backend:
// app.models.accommodation.AccommodationSearchResult /
// PlanningState.accommodation_inventory_report, Step 167D). `offers` can
// only be non-empty when `status === "success"` -- with the default
// not_connected accommodation provider, this is always `status:
// "not_connected"` with an empty `offers` list, and the frontend never
// invents a different value client-side.
export type AccommodationInventoryReport = {
  provider: string;
  status: "success" | "not_connected" | "unavailable" | "failed";
  offers: AccommodationOffer[];
  message: string | null;
};

// Provenance for one scraped (not official-provider) flight offer
// (backend: app.models.scraping.ScrapedDataProvenance, Step 169C/169D).
// `official_provider` is always `false` here -- the backend's own type
// (`Literal[False]`) makes any other value impossible to serialize.
// Present on a `FlightOffer` only when that offer came from
// `ScrapedLocalFlightProvider` (Step 169D), never from an official
// flight provider. Structurally identical to
// `ScrapedAccommodationProvenance` -- kept as a separate type only to
// mirror the backend's own separate `FlightOffer`/`AccommodationOffer`
// models.
export type ScrapedFlightProvenance = {
  source_id: string;
  source_name: string;
  source_type: string;
  provenance: string;
  confidence: "experimental" | "fragile";
  fetched_at: string | null;
  parser_version: string | null;
  source_url: string | null;
  extraction_method: string;
  official_provider: false;
};

// One flight leg (backend: app.models.flight.FlightSegment). Every
// optional field stays `null` unless a real provider/parser actually
// returned it; the frontend never fills one in.
export type FlightSegment = {
  origin_airport: string | null;
  destination_airport: string | null;
  departure_time: string | null;
  arrival_time: string | null;
  carrier_name: string | null;
  carrier_code: string | null;
  flight_number: string | null;
  duration_minutes: number | null;
  data_status: string;
};

// Bookable flight inventory offer (backend: app.models.flight.FlightOffer,
// Step 169A). Every optional field here stays `null`/empty unless a real
// provider actually returned it; the frontend never fills one in.
// `scraped_provenance` (Step 169D) is set only when this offer came from
// the scraped/experimental path (`ScrapedLocalFlightProvider`, the
// default flight provider) rather than an official, connected flight
// provider -- when present, this offer must be labeled as scraped/
// experimental/fragile and never presented as official-provider data.
export type FlightOffer = {
  offer_id: string;
  provider: string;
  data_status: string;
  outbound_segments: FlightSegment[];
  return_segments: FlightSegment[];
  total_price_amount: number | null;
  currency: string | null;
  booking_url: string | null;
  availability_status: string | null;
  baggage_policy: string | null;
  cancellation_policy: string | null;
  source_name: string | null;
  source_url: string | null;
  scraped_provenance: ScrapedFlightProvenance | null;
};

// Bookable flight inventory report for the whole trip (backend:
// app.models.flight.FlightSearchResult /
// PlanningState.flight_inventory_report, Step 169E). `offers` can only be
// non-empty when `status === "success"` -- with the default
// `scraped_local` flight provider and no local HTML file present, this is
// always `status: "unavailable"` with an empty `offers` list, and the
// frontend never invents a different value client-side. Flights are never
// scheduled into daily itinerary experiences -- this report is inventory
// reporting only.
export type FlightInventoryReport = {
  provider: string;
  status: "success" | "not_connected" | "unavailable" | "failed";
  offers: FlightOffer[];
  message: string | null;
};

export type DailyPlan = {
  day_plan_id: string;
  day_number: number;
  date: string;
  experiences: ExperienceItem[];
  restaurant_suggestions: RestaurantSuggestion[];
  accommodation_suggestions: AccommodationSuggestion[];
  warnings: string[];
};

export type StayAreaGuidance = {
  summary: string;
  suggested_anchor_accommodation_pois: AccommodationSuggestion[];
  assumptions: string[];
  warnings: string[];
};

export type DecisionSummary = {
  summary: string;
  provider_backed_facts: string[];
  proximity_based_decisions: string[];
  unvalidated_items: string[];
  user_review_required: string[];
};

export type ImplementationGaps = {
  summary: string;
  connected_data: string[];
  missing_data: string[];
  next_data_needed: string[];
  why_needs_review: string[];
};

export type ChecklistItemStatus =
  | "checked"
  | "needs_review"
  | "missing_data"
  | "not_implemented";

export type ReadinessChecklistItem = {
  label: string;
  status: ChecklistItemStatus;
  explanation: string;
};

export type ReadinessChecklist = {
  summary: string;
  items: ReadinessChecklistItem[];
};

export type RouteSegment = {
  from_place_id: string | null;
  from_name: string | null;
  to_place_id: string | null;
  to_name: string | null;
  travel_mode: string | null;
  distance_meters: number | null;
  duration_minutes: number | null;
  source: string | null;
  data_status: string;
  assumptions: string[];
  warnings: string[];
};

export type DailyRouteFeasibility = {
  day_number: number;
  segments: RouteSegment[];
  data_status: string;
  assumptions: string[];
  warnings: string[];
};

export type RouteFeasibilityContext = {
  source: string | null;
  data_status: string;
  confidence: number;
  daily_route_feasibility: DailyRouteFeasibility[];
  assumptions: string[];
  warnings: string[];
};

export type ExperiencePlanData = {
  trip_id: string;
  experience_plan: {
    daily_plans: DailyPlan[];
    stay_area_guidance: StayAreaGuidance;
    decision_summary: DecisionSummary;
    implementation_gaps: ImplementationGaps;
    readiness_checklist: ReadinessChecklist;
    route_feasibility_context: RouteFeasibilityContext;
    assumptions: string[];
    confidence: number;
  };
};

export type ValidationIssue = {
  category: string;
  severity: "critical" | "warning" | "suggestion";
  message: string;
  affected_section: string | null;
  suggested_fix: string | null;
};

export type ValidationReport = {
  readiness_status: "ready" | "needs_review" | "blocked";
  critical_issues: ValidationIssue[];
  warnings: ValidationIssue[];
  provider_coverage_notes: string[];
  unavailable_data_notes: string[];
};

export type ValidationReportData = {
  trip_id: string;
  validation_report: ValidationReport;
};

export type ProviderStatusEntry = {
  provider_name: string;
  provider_type: string;
  status: string;
  data_status: string;
  unavailable_fields: string[];
};

export type UnavailableDataItem = {
  field: string;
  reason: string;
  data_status: string;
};

export type ProviderCoverageData = {
  trip_id: string;
  provider_coverage: ProviderCoverage;
  provider_status: Record<string, ProviderStatusEntry>;
  unavailable_data: UnavailableDataItem[];
  data_sources_used: string[];
};

// Deterministic, honest preview of what a future regeneration step would
// likely need to change -- never something this endpoint applies itself
// (backend: FeedbackService.apply_feedback / _LIKELY_CHANGES_BY_TYPE).
export type FeedbackChangePreview = {
  preview_status: string;
  would_require_regeneration: boolean | null;
  likely_changes: string[];
  unchanged_sections: string[];
  blocked_by: string[];
};

// Preliminary, deterministic rule-based classification only -- never an AI
// interpretation, and never something applied to the plan
// (backend: FeedbackService._classify).
export type FeedbackInterpretation = {
  method: string;
  applied_to_plan: boolean;
  summary: string;
  matched_labels: string[];
  note: string;
  change_preview?: FeedbackChangePreview;
};

export type FeedbackEvent = {
  feedback_event_id: string;
  feedback_text: string;
  feedback_type: string | null;
  handling_status: string;
  regeneration_strategy: string;
  affected_stages: string[];
  interpretation: FeedbackInterpretation | null;
  created_at: string;
};

// One feedback_type group inside PendingFeedbackSummary.summary_items
// (backend: PendingFeedbackSummaryItem). `likely_changes` restates the same
// deterministic per-type text already shown on individual feedback events --
// never a new claim, never something applied to the plan.
export type PendingFeedbackSummaryItem = {
  feedback_type: string;
  count: number;
  example_feedback: string;
  likely_changes: string[];
};

// Plan-level, deterministic rollup of feedback_history (backend:
// PendingFeedbackSummary / FeedbackService._compute_pending_feedback_summary).
// Purely a restatement of already-captured feedback -- never applied to the
// plan, never a claim of regeneration.
export type PendingFeedbackSummary = {
  status: string;
  total_feedback_items: number;
  feedback_type_counts: Record<string, number>;
  affected_stages: string[];
  requires_regeneration: boolean;
  latest_feedback_at: string | null;
  summary_items: PendingFeedbackSummaryItem[];
  blocked_by: string[];
  note: string;
};

// A single "keep this place" instruction stored for a possible future
// regeneration (backend: app.models.planning_state.UserLock). Creating or
// removing a lock never changes the current plan itself -- see
// app.tests.api.test_trip_locks.test_locking_does_not_modify_generated_plan_sections.
export type UserLock = {
  lock_id: string;
  locked_item_type: string;
  locked_item_id: string;
  reason: string;
  is_active: boolean;
  created_at: string;
  removed_at: string | null;
};

// One recorded plan version (backend: app.models.planning_state.
// VersionHistoryItem). Purely bookkeeping about which pipeline sections
// were produced/changed -- never a snapshot of their travel-fact content,
// and never itself a claim that a new version was created from feedback.
export type VersionHistoryItem = {
  version_id: string;
  version_label: string;
  created_by: string;
  summary: string | null;
  changed_sections: string[];
  preserved_sections: string[];
  feedback_event_id: string | null;
  created_at: string;
};

// A safe, minimal representation of one active UserLock inside
// PlanDiffPreview.would_preserve_locked_items -- only the fields already on
// UserLock itself, never a snapshot of the locked item's actual travel data
// (backend: app.models.planning_state.PreservedLockedItem).
export type PreservedLockedItem = {
  locked_item_type: string;
  locked_item_id: string;
  reason: string;
};

// Deterministic, honest preview of what a *future* regeneration would
// compare/change (backend: app.models.planning_state.PlanDiffPreview /
// app.services.plan_diff_preview_service.PlanDiffPreviewService). Recomputed
// from scratch on the backend from version_history/feedback_history/
// user_locks -- never something this endpoint applies itself, never a claim
// that a new version or diff was actually generated.
export type PlanDiffPreview = {
  preview_status: string;
  from_version: string | null;
  to_version: string | null;
  regeneration_available: boolean;
  would_create_version: string | null;
  triggered_by_feedback_event_ids: string[];
  pending_feedback_count: number;
  active_lock_count: number;
  would_consider_sections: string[];
  would_preserve_locked_items: PreservedLockedItem[];
  blocked_by: string[];
  note: string;
};

// Deterministic, honest gate explaining whether feedback-driven
// regeneration can run right now (backend: app.models.planning_state.
// RegenerationReadiness / app.services.regeneration_readiness_service.
// RegenerationReadinessService). `status` stays "blocked" and
// `can_regenerate` stays false today because no real regeneration engine
// is connected -- this is a readout only, never something applied by the
// frontend, and never a claim that regeneration ran or a plan changed.
export type RegenerationReadiness = {
  status: string;
  can_regenerate: boolean;
  current_version: string | null;
  would_create_version: string | null;
  pending_feedback_count: number;
  active_lock_count: number;
  required_inputs: string[];
  available_inputs: string[];
  missing_capabilities: string[];
  blocked_by: string[];
  next_step: string;
};

export type RegenerationReadinessData = {
  trip_id: string;
  regeneration_readiness: RegenerationReadiness;
};

// One audit record of a blocked `POST /trips/{trip_id}/regenerate` call
// (backend: app.models.planning_state.RegenerationAttempt /
// app.services.regeneration_attempt_service.RegenerationAttemptService).
// Purely bookkeeping proving an attempt was requested and refused -- never
// itinerary content, and never a claim that regeneration ran, a diff was
// generated, or a new plan version was created.
export type RegenerationAttempt = {
  attempt_id: string;
  status: string;
  requested_at: string;
  current_version: string | null;
  would_create_version: string | null;
  pending_feedback_count: number;
  active_lock_count: number;
  reason_code: string;
  message: string;
};

export type RegenerationAttemptsData = {
  trip_id: string;
  regeneration_attempts: RegenerationAttempt[];
};

// Real backend PlanningOrchestrator pipeline stage-progress bookkeeping
// (backend: app.models.planning_state.GenerationProgress, Step 163B) --
// which stage of POST /trips/{trip_id}/generate is running or has run.
// This is never flight tracking, a real flight route, a real route/travel
// time, or a booking status; `is_real_backend_stage_progress` is a fixed
// safety marker confirming that. Consumed only by the Step 163A/163C
// decorative loading animation as an optional data source, never as a
// claim of real travel progress.
export type GenerationProgress = {
  status: "idle" | "generating" | "completed" | "failed";
  current_stage: string | null;
  current_stage_label: string | null;
  completed_stages: string[];
  total_stages: number;
  progress_percent: number;
  message: string;
  updated_at: string;
  is_real_backend_stage_progress: boolean;
};

export type GenerationProgressData = {
  trip_id: string;
  generation_progress: GenerationProgress;
};

// One AI-proposed candidate's review state (Step 170A, extended with
// deterministic eligibility in Step 170B; backend:
// app.models.ai_candidate_review.AICandidateReviewItem). Never carries a
// price, rating, opening hour, route time, review count, booking link, or
// safety score -- those fields don't exist on the backend model this
// mirrors. `eligible_for_promotion=true` means this candidate cleared
// deterministic provider-grounding/quality rules -- it is not a claim
// that the candidate is booked, verified, or has been scheduled.
export type AICandidateReviewItem = {
  candidate_id: string;
  name: string;
  category: string | null;
  source: string;
  ai_proposed: boolean;
  provider_grounded: boolean;
  quality_bucket: string | null;
  grounding_status: string | null;
  eligible_for_promotion: boolean;
  eligibility_reasons: string[];
  rejection_reasons: string[];
  warnings: string[];
};

// Read-only AI candidate review report (Step 170A/170B; backend:
// app.models.ai_candidate_review.AICandidateReviewReport). `status` stays
// `"no_candidate_data"` honestly when no AI candidate discovery has run
// for this trip -- never fabricated. This report never implies a
// candidate has been scheduled or promoted; see AICandidatePromotionReport
// for that separate, explicit step.
export type AICandidateReviewReport = {
  trip_id: string;
  status: string;
  total_ai_candidates: number;
  grounded_candidates: number;
  ungrounded_candidates: number;
  eligible_for_promotion: number;
  items: AICandidateReviewItem[];
  generated_at: string;
};

export type AICandidateReviewData = {
  trip_id: string;
  ai_candidate_review_report: AICandidateReviewReport;
};

// One AI candidate that cleared Step 170B's deterministic eligibility
// rules and was materialized by Step 170C (backend:
// app.models.ai_candidate_promotion.PromotedAICandidate). `promoted` is
// always `true` here. `coordinates`/`confidence`/`data_status` (Step
// 170D) are copied verbatim from the real GroundedCandidate evidence used
// to ground this candidate -- never guessed. Being promoted is still not
// the same as being scheduled into an itinerary day; see
// ExperienceItem.promoted_from_ai for that.
export type PromotedAICandidate = {
  candidate_id: string;
  name: string;
  category: string | null;
  source: string;
  provider_place_id: string | null;
  provider_source: string | null;
  original_ai_candidate_id: string | null;
  quality_bucket: string | null;
  grounding_status: string | null;
  coordinates: GeoPoint | null;
  confidence: number | null;
  data_status: string | null;
  promotion_reasons: string[];
  warnings: string[];
  promoted: boolean;
};

// Deterministic AI candidate promotion report (Step 170C; backend:
// app.models.ai_candidate_promotion.AICandidatePromotionReport). `status`
// stays honestly `"no_candidates_reviewed"`/`"no_eligible_candidates"`
// when there is nothing to promote -- never fabricated. This report is
// never itself an itinerary mutation -- see docs/13_llm_reasoning_
// pipeline.md section 83 for how (and only how) a promoted candidate can
// later be scheduled by the backend's own existing rules.
export type AICandidatePromotionReport = {
  trip_id: string;
  status: string;
  total_reviewed_candidates: number;
  promoted_count: number;
  skipped_count: number;
  promoted_candidates: PromotedAICandidate[];
  skipped_candidate_ids: string[];
  generated_at: string;
};

export type AICandidatePromotionData = {
  trip_id: string;
  ai_candidate_promotion_report: AICandidatePromotionReport;
};

// Full PlanningState is much larger than this; only feedback_history,
// pending_feedback_summary, user_locks, version_history, plan_diff_preview,
// regeneration_readiness, regeneration_attempts,
// accommodation_inventory_report, flight_inventory_report, and
// ai_candidate_promotion_report are declared here since that's the only
// part of it the frontend reads.
export type TripData = {
  trip_id: string;
  planning_state: {
    feedback_history: FeedbackEvent[];
    pending_feedback_summary: PendingFeedbackSummary;
    user_locks: UserLock[];
    version_history: VersionHistoryItem[];
    plan_diff_preview: PlanDiffPreview;
    regeneration_readiness: RegenerationReadiness;
    regeneration_attempts: RegenerationAttempt[];
    accommodation_inventory_report: AccommodationInventoryReport | null;
    flight_inventory_report: FlightInventoryReport | null;
    ai_candidate_promotion_report: AICandidatePromotionReport | null;
  };
};
