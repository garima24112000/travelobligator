export type TravelGroupType =
  | "solo"
  | "couple"
  | "family"
  | "friends"
  | "group";

export type TripPace = "relaxed" | "balanced" | "packed";

export type AccommodationType =
  | "hotel"
  | "airbnb"
  | "hostel"
  | "resort"
  | "no_preference";

export type TransportPreference =
  | "public_transport"
  | "taxi"
  | "self_drive"
  | "train"
  | "flight"
  | "no_preference";

export type TripRequest = {
  id?: string;
  destination: string;
  originCity?: string;
  startDate: string;
  endDate: string;
  travelersCount: number;
  travelGroupType: TravelGroupType;
  budgetMin?: number;
  budgetMax?: number;
  pace: TripPace;
  accommodationType: AccommodationType;
  transportPreference: TransportPreference;
  interests: string[];
  mustVisit: string[];
  mustAvoid: string[];
  constraints: string[];
  freeTextPreferences?: string;
};

export type TripGenerationMetadata = {
  generatedAt: string;
  source: string;
  warning: string;
};

export type AccommodationRecommendation = {
  id: string;
  name: string;
  type: AccommodationType;
  neighborhood: string;
  nightlyPrice: number;
  currency: string;
  rating: number;
  bookingUrl: string;
  reasons: string[];
  amenities: string[];
  latitude?: number;
  longitude?: number;
  isMockData: boolean;
};

export type ItineraryActivity = {
  id: string;
  name: string;
  category: string;
  startTime: string;
  endTime: string;
  durationMinutes: number;
  latitude: number;
  longitude: number;
  whyIncluded: string;
  travelFromPrevious?: {
    mode: string;
    timeMinutes: number;
    distanceKm?: number;
  };
};

export type ItineraryDay = {
  dayNumber: number;
  date?: string;
  baseCity: string;
  theme: string;
  activities: ItineraryActivity[];
  foodSuggestions: string[];
  notes: string[];
  totalTravelTimeMinutes: number;
  estimatedCost: number;
};

export type TransportStrategy = {
  localTransport: string;
  intercityTransport?: string;
  rationale: string[];
};

export type BudgetBreakdown = {
  stay: number;
  food: number;
  transport: number;
  activities: number;
  misc: number;
  total: number;
  currency: string;
};

export type Itinerary = {
  id: string;
  tripRequestId?: string;
  versionNumber: number;
  tripSummary: {
    destination: string;
    durationDays: number;
    travelStyle: string;
    summaryText: string;
  };
  stayRecommendation: {
    recommendedArea: string;
    reasons: string[];
    topAccommodations: AccommodationRecommendation[];
  };
  transportStrategy: TransportStrategy;
  dailyPlan: ItineraryDay[];
  importantTips: string[];
  alternatives: string[];
  estimatedBudgetBreakdown: BudgetBreakdown;
  isMockData: boolean;
};

export type TripFeedback = {
  itineraryId: string;
  feedbackText: string;
  quickAction?:
    | "make_cheaper"
    | "make_relaxed"
    | "add_hidden_gems"
    | "more_luxury"
    | "replace_hotel"
    | "reduce_walking"
    | "prioritize_food"
    | "prioritize_nature";
};

export type TripGenerationResponse = {
  tripRequest: TripRequest;
  itinerary: Itinerary;
  metadata: TripGenerationMetadata;
};
