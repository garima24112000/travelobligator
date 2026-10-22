// The limited-beta labelling shown on the sign-in screen and in the signed-in page, and the password-loss
// warning shown at registration.
//
// Presentation copy only. It adds no claim of its own about a plan: readiness, review and blocked states,
// material warnings and uncovered interests are still stated, per itinerary, by the backend's validation
// report and the banners built from it. Nothing here may call an itinerary complete, verified or ready to
// follow, and nothing here promises a feature that does not exist (password recovery does not).
//
// Kept free of React and of runtime imports so it can be unit tested with Node's built-in test runner
// (frontend/tests).

export const BETA_LABEL = "Limited beta";

/** One sentence, shown beside the product name before and after sign-in. */
export const BETA_SUMMARY =
  "An experimental travel planner in limited beta. Itineraries are built with external providers and AI, and need your own checks before you rely on them.";

export const BETA_NOTICE_HEADING = "What to know during this beta";

export const BETA_NOTICE_POINTS: string[] = [
  "TravelObligator is an experimental travel planner.",
  "It uses external travel-data providers and AI to construct itineraries.",
  "Each place in an itinerary was identified by a travel-data provider. Its current existence, accessibility, opening hours, ticket availability and transport options are not independently guaranteed.",
  "An itinerary marked as needing review needs your own judgement before you follow it.",
  "Confirm the details that matter to your trip before relying on a plan.",
];

/** Shown beside the registration form. The privacy notice says the same thing in the same words. */
export const PASSWORD_LOSS_WARNING =
  "Password recovery is not available during this beta. If you lose your password, you may lose access to your saved trips and may be unable to delete your account yourself.";
