// The privacy notice shown on the sign-in screen and in the account area, and the wording of the
// trip / account deletion controls.
//
// Every statement here describes what the application verifiably does (see
// docs/22_free_tier_deployment.md section 13B and backend/app/services/data_deletion_service.py). It
// makes no legal or compliance claim, names no retention period the project has not set, and gives no
// contact detail that is not the project's own public repository. Change the wording here only together
// with the behaviour it describes.
//
// Kept free of React and of runtime imports so it can be unit tested with Node's built-in test runner
// (frontend/tests).

/** The same sentence the registration form shows (`PASSWORD_LOSS_WARNING` in lib/beta-notice.ts). It is
 * written out here because this module imports nothing; a unit test keeps the two identical. */
export const PRIVACY_PASSWORD_LOSS_POINT =
  "Password recovery is not available during this beta. If you lose your password, you may lose access to your saved trips and may be unable to delete your account yourself.";

export const PROJECT_REPOSITORY_URL = "https://github.com/garima24112000/travelobligator";
export const PROJECT_ISSUES_URL = `${PROJECT_REPOSITORY_URL}/issues`;

export type PrivacySection = { heading: string; points: string[] };

export const PRIVACY_NOTICE_TITLE = "Your data and privacy";

export const PRIVACY_NOTICE_INTRO =
  "TravelObligator is a free portfolio and research project in limited beta, not a commercial service. This is a plain description of what it stores and what you can delete.";

export const PRIVACY_NOTICE: PrivacySection[] = [
  {
    heading: "What is stored",
    points: [
      "Your account: your email address and a salted hash of your password. The password itself is not stored.",
      "Your trips: what you enter (destination, origin, dates, travellers, budget, pace, interests, must-visit places, places to avoid, constraints and notes), the itineraries built from it with their earlier versions, and any change requests and \"keep\" markers you add.",
      "In your browser: one sign-in cookie, used only to keep you signed in, which expires after seven days; when you open a beta invitation link, an invitation cookie that only allows creating an account from that browser, which expires after at most 30 minutes and is removed once the account is created; and your choice of view, kept in the browser's local storage. These are the only things this application's own code stores there. The services it relies on (its hosting, and the map provider when a map is shown) may set or read their own cookies or similar data; this project has not checked what they do.",
      "The application's own logs: these are written to record technical events with account and trip identifiers, and are designed to leave out your email address, your password and the text of your trips. That has been checked in the code and its tests, not by inspecting the logs of the running service, and an unexpected error report could still contain fragments of a request.",
      "Logs kept by the hosting services: these are separate from the application's own. Such logs commonly include IP addresses and the web addresses requested, which contain trip identifiers. This project has not independently verified what they record or for how long.",
      "Short-lived counters used to limit abuse, such as failed sign-in attempts. They are stored as keyed one-way hashes, not as your email address.",
    ],
  },
  {
    heading: "What is sent to other services",
    points: [
      "To build an itinerary, trip details such as the destination, dates, interests, must-visit places and constraints are sent to external travel-data providers (Geoapify for places and routes; Open-Meteo, Nager.Date and Frankfurter for weather, holidays and currency) and, for choosing and describing stops, to AI providers (Groq and Google Gemini).",
      "If you ask for changes to an itinerary, the text of your request may also be sent to an AI provider, where that feature is switched on.",
      "Your email address and password are not sent to these providers. How they handle what they receive is governed by their own terms, which this project does not control.",
      "Maps are drawn with tiles loaded directly by your browser from OpenStreetMap, and map links open Google Maps or OpenStreetMap. Those services see your browser's request.",
      "The app runs on third-party hosting (Vercel, Render, Neon and Upstash), which process this data to operate it.",
    ],
  },
  {
    heading: "Deleting your data",
    points: [
      "Delete a trip with the Delete button next to it in My trips. The trip, its itineraries and versions, and its change requests are removed from the database straight away.",
      "Delete your account under \"Delete account\" below, by entering your password. Your account and every trip in it are removed from the database straight away, and you are signed out.",
      "Deletion cannot be undone, and deleted trips cannot be recovered.",
    ],
  },
  {
    heading: "Limits you should know about",
    points: [
      "A trip cannot be deleted while its itinerary is being built. Wait for it to finish and try again.",
      "Deleting removes your records from this app's own database, and only from there. Copies that exist elsewhere, described in the next three points, are not changed by it.",
      "Database exports that the project operator keeps: before a new version is deployed, the operator may export a copy of the database so that it can be restored if something goes wrong. Such an export may be kept temporarily for that purpose. It is not edited when you delete a trip or your account, so something you deleted can remain in an export made before you deleted it, until that export is disposed of. This project has not yet set a retention period for these exports.",
      "Backups and logs kept by the hosting services: deletion does not reach them. They are removed on those services' own schedules, which this project has not verified.",
      "What was already sent to the external services above to build an itinerary cannot be recalled. They keep it according to their own policies.",
      "To keep daily limits fair, the time of a recent itinerary request is kept for up to 24 hours after its trip or account is deleted, without the trip and, once the account is deleted, without any link to you.",
      PRIVACY_PASSWORD_LOSS_POINT,
      "The project currently has no verified way to confirm that an account belongs to someone who cannot sign in to it, so it does not delete accounts on request for people who have lost their password.",
    ],
  },
  {
    heading: "Questions",
    points: [
      "This project has no support desk. You can open an issue on the project's public GitHub repository. Issues are public, so do not include personal details there.",
    ],
  },
];

// -- deletion controls ------------------------------------------------------------------------------

export function tripDeletionQuestion(destination: string | null): string {
  const name = destination && destination.trim() ? destination.trim() : "this trip";
  return `Delete ${name}? Its itinerary, earlier versions and change requests will be removed. This cannot be undone.`;
}

export const TRIP_DELETED_MESSAGE = "Trip deleted.";
export const TRIP_DELETE_FAILED_MESSAGE = "We couldn't delete that trip. Nothing was removed. Please try again.";

export const ACCOUNT_DELETION_WARNING =
  "This permanently deletes your account and every trip in it. It cannot be undone.";
export const ACCOUNT_DELETION_CHECKBOX_LABEL =
  "I understand that my account and all my trips will be permanently deleted.";
export const ACCOUNT_DELETED_MESSAGE = "Your account and trips were deleted.";
export const ACCOUNT_DELETE_FAILED_MESSAGE =
  "We couldn't delete your account. Nothing was removed. Please try again.";

export type AccountDeletionInput = { password: string; confirmed: boolean };

/** Why the account-deletion form cannot be submitted yet, or null when it can. */
export function accountDeletionBlocker(input: AccountDeletionInput): string | null {
  if (!input.password) return "Enter your password to confirm it is you.";
  if (!input.confirmed) return "Tick the box to confirm you want to delete your account.";
  return null;
}
