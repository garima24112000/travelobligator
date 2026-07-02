# Pipeline Data Flow

## 1. Purpose

This document defines how data moves through the TravelObligator planning pipeline.

It answers:

- Which stage owns which data?
- Which stage consumes which fields?
- Which stage produces which object?
- Which stages use AI?
- Which stages use deterministic logic?
- Which stages use provider data?
- What gets passed forward?

## 2. End-to-End Pipeline

User Input
→ Traveler Profile
→ Planning State
→ Trip Strategy
→ Stay + Transport
→ Experience Planner
→ Plan Validator
→ Feedback Pipeline
→ Updated Planning State
→ Final Itinerary

## 3. Stage 1: User Input → Traveler Profile

## 4. Stage 2: Traveler Profile → Trip Strategy

## 5. Stage 3: Trip Strategy → Stay + Transport

## 6. Stage 4: Stay + Transport → Experience Planner

## 7. Stage 5: Experience Planner → Plan Validator

## 8. Stage 6: Plan Validator → Feedback Pipeline

## 9. Stage 7: Feedback Pipeline → Updated Plan

## 10. Shared Objects

## 11. Data Ownership Rules

## 12. Design Principles
