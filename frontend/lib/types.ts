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
};

export type ExperienceItem = {
  experience_id: string;
  name: string;
  category: string;
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

export type ExperiencePlanData = {
  trip_id: string;
  experience_plan: {
    daily_plans: DailyPlan[];
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
