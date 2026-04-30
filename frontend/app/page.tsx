import type {
  AccommodationRecommendation,
  Itinerary,
  ItineraryDay,
  TripRequest,
} from "../../shared/types";
import { TravelCopilotShell } from "../components/travel-copilot-shell";

const tripRequest: TripRequest = {
  id: "trip-request-demo-01",
  destination: "Lisbon, Portugal",
  originCity: "New York",
  startDate: "2026-06-12",
  endDate: "2026-06-18",
  travelersCount: 2,
  travelGroupType: "couple",
  budgetMin: 1800,
  budgetMax: 3200,
  pace: "balanced",
  accommodationType: "hotel",
  transportPreference: "public_transport",
  interests: ["food", "walkable neighborhoods", "history", "sunset views"],
  mustVisit: ["Alfama", "Belém Tower", "Time Out Market"],
  mustAvoid: ["late-night clubbing", "overpacked museum days"],
  constraints: ["limit transit to under 35 minutes between key stops"],
  freeTextPreferences:
    "We want a romantic, walkable trip with great food, easy transit, and one or two hidden-gem experiences.",
};

const accommodationRecommendations: AccommodationRecommendation[] = [
  {
    id: "stay-1",
    name: "Miradouro House Hotel",
    type: "hotel",
    neighborhood: "Baixa",
    nightlyPrice: 214,
    currency: "USD",
    rating: 4.8,
    bookingUrl: "https://example.com/demo-stay-1",
    reasons: [
      "Central base for short transfers",
      "Strong rooftop views for a couple trip",
      "Fits the current mock budget",
    ],
    amenities: ["rooftop", "breakfast", "walkable"],
    latitude: 38.7139,
    longitude: -9.1397,
    isMockData: true,
  },
  {
    id: "stay-2",
    name: "Riverside Design Stay",
    type: "hotel",
    neighborhood: "Cais do Sodré",
    nightlyPrice: 196,
    currency: "USD",
    rating: 4.7,
    bookingUrl: "https://example.com/demo-stay-2",
    reasons: ["Good nightlife access without relying on taxis", "Easy public transport connections"],
    amenities: ["river views", "metro nearby", "boutique"],
    latitude: 38.7062,
    longitude: -9.145,
    isMockData: true,
  },
  {
    id: "stay-3",
    name: "Alfama Lane Boutique",
    type: "hotel",
    neighborhood: "Alfama",
    nightlyPrice: 178,
    currency: "USD",
    rating: 4.6,
    bookingUrl: "https://example.com/demo-stay-3",
    reasons: ["Great character and old-town feel", "Best for evening strolls and food stops"],
    amenities: ["historic", "quiet evenings", "local cafe"],
    latitude: 38.7141,
    longitude: -9.1303,
    isMockData: true,
  },
];

const dailyPlan: ItineraryDay[] = [
  {
    dayNumber: 1,
    date: "2026-06-12",
    baseCity: "Lisbon",
    theme: "Arrival and first look",
    activities: [
      {
        id: "day1-act1",
        name: "Check-in and neighborhood walk",
        category: "Orientation",
        startTime: "15:00",
        endTime: "17:00",
        durationMinutes: 120,
        latitude: 38.7139,
        longitude: -9.1397,
        whyIncluded: "Keeps the first day light after arrival while establishing the trip base.",
      },
      {
        id: "day1-act2",
        name: "Sunset lookout at Miradouro",
        category: "Scenic",
        startTime: "18:00",
        endTime: "19:30",
        durationMinutes: 90,
        latitude: 38.7159,
        longitude: -9.1292,
        whyIncluded: "Gives the trip a strong first impression and an easy evening activity.",
        travelFromPrevious: {
          mode: "Walk",
          timeMinutes: 15,
          distanceKm: 1.1,
        },
      },
    ],
    foodSuggestions: ["Neighborhood seafood bistro", "Quick pastel de nata stop"],
    notes: ["Keep pace relaxed after the flight.", "Use this day to adjust to the city rhythm."],
    totalTravelTimeMinutes: 15,
    estimatedCost: 118,
  },
  {
    dayNumber: 2,
    date: "2026-06-13",
    baseCity: "Lisbon",
    theme: "Historic core and food focus",
    activities: [
      {
        id: "day2-act1",
        name: "Alfama district wander",
        category: "Neighborhood",
        startTime: "09:30",
        endTime: "12:00",
        durationMinutes: 150,
        latitude: 38.7126,
        longitude: -9.1273,
        whyIncluded: "Fits the trip's history and walkability goals.",
      },
      {
        id: "day2-act2",
        name: "Time Out Market lunch",
        category: "Food",
        startTime: "12:30",
        endTime: "14:00",
        durationMinutes: 90,
        latitude: 38.7079,
        longitude: -9.1456,
        whyIncluded: "Concentrates food discovery without adding routing complexity.",
        travelFromPrevious: {
          mode: "Tram",
          timeMinutes: 18,
          distanceKm: 2.8,
        },
      },
      {
        id: "day2-act3",
        name: "Riverside evening stroll",
        category: "Leisure",
        startTime: "18:00",
        endTime: "19:30",
        durationMinutes: 90,
        latitude: 38.7051,
        longitude: -9.1436,
        whyIncluded: "Balances the day after lunch-heavy exploration.",
        travelFromPrevious: {
          mode: "Walk",
          timeMinutes: 12,
          distanceKm: 0.9,
        },
      },
    ],
    foodSuggestions: ["Seafood tascas", "Local wine bar"],
    notes: ["Keep one activity flexible for a hidden gem.", "Walk between compact points when possible."],
    totalTravelTimeMinutes: 30,
    estimatedCost: 146,
  },
  {
    dayNumber: 3,
    date: "2026-06-14",
    baseCity: "Lisbon",
    theme: "Belém landmarks and low-effort transit",
    activities: [
      {
        id: "day3-act1",
        name: "Belém Tower and waterfront",
        category: "Landmark",
        startTime: "10:00",
        endTime: "12:00",
        durationMinutes: 120,
        latitude: 38.6916,
        longitude: -9.2159,
        whyIncluded: "Provides a classic landmark day without overloading the itinerary.",
      },
      {
        id: "day3-act2",
        name: "Monastery courtyard stop",
        category: "Culture",
        startTime: "12:30",
        endTime: "13:30",
        durationMinutes: 60,
        latitude: 38.6979,
        longitude: -9.2063,
        whyIncluded: "Adds variety while remaining close to the main landmark cluster.",
        travelFromPrevious: {
          mode: "Bus",
          timeMinutes: 11,
          distanceKm: 1.4,
        },
      },
    ],
    foodSuggestions: ["Custard tart cafe", "Light seafood lunch"],
    notes: ["This day is intentionally route-efficient.", "Leave room for a slow coffee break."],
    totalTravelTimeMinutes: 11,
    estimatedCost: 132,
  },
];

const itinerary: Itinerary = {
  id: "itinerary-demo-01",
  tripRequestId: tripRequest.id,
  versionNumber: 1,
  tripSummary: {
    destination: tripRequest.destination,
    durationDays: 3,
    travelStyle: "Balanced romantic city break",
    summaryText:
      "Mock itinerary designed around walkable neighborhoods, strong food stops, and low-friction routes.",
  },
  stayRecommendation: {
    recommendedArea: "Baixa / Cais do Sodré",
    reasons: [
      "Keeps transfers short for the current mock route plan",
      "Works for food, sightseeing, and an easy evening return",
      "Offers a good balance between comfort and access",
    ],
    topAccommodations: accommodationRecommendations,
  },
  transportStrategy: {
    localTransport: "Mix of walking, tram, and short metro hops",
    rationale: [
      "Best match for compact city routes",
      "Reduces trip friction between key attractions",
      "Supports a balanced pace without relying on cars",
    ],
  },
  dailyPlan,
  importantTips: [
    "Book the most popular food stops early if they fill quickly.",
    "Keep one evening unstructured for feedback-based changes later.",
    "Use the transit plan to preserve the walkable feel of the trip.",
  ],
  alternatives: ["Swap Belém for a beach day", "Replace one museum stop with a local market"],
  estimatedBudgetBreakdown: {
    stay: 620,
    food: 260,
    transport: 88,
    activities: 156,
    misc: 96,
    total: 1220,
    currency: "USD",
  },
  isMockData: true,
};

const quickActions = [
  {
    label: "Make it cheaper",
    description: "Nudge stay, food, and activity picks toward a lower total spend.",
  },
  {
    label: "Make it more relaxed",
    description: "Trim day density and preserve more open time blocks.",
  },
  {
    label: "Add hidden gems",
    description: "Swap one mainstream stop for a more local experience.",
  },
  {
    label: "Reduce walking",
    description: "Favor short transit hops and tighter geographic clustering.",
  },
] as const;

const transportRecommendations = [
  "Primary mode: walk first, tram second, metro for longer cross-city hops.",
  "Use one-day transit passes only if you plan more than 4 medium-distance rides.",
  "Avoid backtracking by grouping nearby attractions into the same day block.",
];

export default function Home() {
  return (
    <TravelCopilotShell
      itinerary={itinerary}
      preferences={{
        destination: tripRequest.destination,
        dates: `${tripRequest.startDate} to ${tripRequest.endDate}`,
        travelers: `${tripRequest.travelersCount} travelers · ${tripRequest.travelGroupType}`,
        groupType: tripRequest.travelGroupType,
        pace: tripRequest.pace,
        accommodationType: tripRequest.accommodationType,
        transportPreference: tripRequest.transportPreference,
        budget: `${tripRequest.budgetMin} - ${tripRequest.budgetMax} USD`,
        interests: tripRequest.interests,
        mustVisit: tripRequest.mustVisit,
        mustAvoid: tripRequest.mustAvoid,
        notes: tripRequest.freeTextPreferences ?? "",
      }}
      accommodationRecommendations={accommodationRecommendations}
      transportRecommendations={transportRecommendations}
      quickActions={quickActions.map((action) => ({
        label: action.label,
        description: action.description,
      }))}
    />
  );
}