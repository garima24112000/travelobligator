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

// Full PlanningState is much larger than this; only feedback_history and
// pending_feedback_summary are declared here since that's the only part of
// it the frontend reads.
export type TripData = {
  trip_id: string;
  planning_state: {
    feedback_history: FeedbackEvent[];
    pending_feedback_summary: PendingFeedbackSummary;
  };
};
