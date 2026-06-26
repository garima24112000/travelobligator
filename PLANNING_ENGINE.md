# TravelObligator Planning Engine

## 1. User Problem

Travel planning is difficult because users do not just need a list of places. They need a plan that fits their personal constraints.

Most existing itineraries are:

- generic
- static
- not route-aware
- not budget-aware
- not easy to modify
- scattered across blogs, maps, hotel sites, and transport apps

TravelObligator should solve this by turning a user's preferences, constraints, budget, and travel style into a realistic, editable, map-aware trip plan.

The goal is not to tell users every possible thing they can do. The goal is to help them make better travel decisions faster.

The planning engine should answer:

- What should this traveler do each day?
- Where should they stay?
- How should they move around?
- What should they avoid?
- Is the itinerary realistic?
- How should the plan change if the user dislikes part of it?

## 2. Traveler Profile

The traveler profile is the structured representation of what the user wants from the trip.

It should be created from both:

- form inputs
- free-text preferences

The traveler profile should include:

### Basic Trip Context

- destination
- origin city
- start date
- end date
- trip duration
- number of travelers
- travel group type: solo, couple, family, friends, group

### Budget Context

- minimum budget
- maximum budget
- budget tier: budget, mid-range, premium, luxury
- accommodation budget
- transport budget
- food/activity flexibility

### Travel Style

- pace: relaxed, balanced, packed
- preference for iconic attractions vs hidden gems
- preference for structured plans vs flexible exploration
- morning-heavy vs evening-heavy travel
- comfort vs cost sensitivity

### Interests

Examples:

- food
- culture
- history
- nature
- nightlife
- shopping
- beaches
- adventure
- museums
- scenic views
- family-friendly activities

### Stay Preferences

- hotel
- Airbnb
- hostel
- resort
- central location
- quiet neighborhood
- near public transport
- family-friendly
- work-friendly
- luxury-oriented

### Transport Preferences

- public transport
- taxi
- self-drive
- train
- domestic flight
- walking
- no preference

### Special Needs or Constraints

- limited walking
- elderly travelers
- children
- accessibility needs
- dietary preferences
- safety concerns
- avoid late nights
- avoid crowded places

### Non-Negotiables

- must-visit places
- must-avoid places or experiences
- fixed events or reservations
- required free time blocks

## 3. Hard Constraints

## 4. Soft Preferences

## 5. Destination Intelligence

## 6. POI Ranking Logic

## 7. Accommodation Ranking Logic

## 8. Transport Decision Logic

## 9. Route Feasibility Logic

## 10. Budget Logic

## 11. Itinerary Generation Flow

## 12. Validation Checks

## 13. Feedback Regeneration Logic
