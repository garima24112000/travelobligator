// The privacy notice shown on the sign-in screen and in the account area, and the wording of the
// trip / account deletion controls.
//
// Every statement here describes what the application verifiably does (see
// docs/22_free_tier_deployment.md section 13B and backend/app/services/data_deletion_service.py). It
// makes no legal or compliance claim, states no retention period that is not in force (the one it states
// for backups is the automated backups' expiry, docs/25_operations_runbook.md section 2.6), and gives no
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

/** Where someone who cannot sign in asks for their account to be deleted (owner decision D3a; the procedure is
 * docs/25_operations_runbook.md section 8.1, carried out with backend/scripts/locked_out_account_deletion.py). */
export const ACCOUNT_DELETION_CONTACT = "garimaprachi2411@gmail.com";
/** The longest a deletion code stays valid (`MAX_HOURS` of that command). */
export const ACCOUNT_DELETION_CODE_HOURS = 72;

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
      "Beta invitation links: the code in a link comes after the \"#\" in its address, a part that browsers do not send to any server when they load a page. The page removes it from the address bar straight away and sends it to this app once, to check it. The app does not record that an account was created through an invitation link, and the invitation cookie holds a one-way fingerprint of the code, not the code itself. Anyone who has the link can use it to create an account, so please do not pass it on.",
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
      "The app runs on third-party hosting (Vercel, Render, Neon and Upstash), which process this data to operate it. Its daily backups are made by an automated job on GitHub's servers and stored, encrypted, with Amazon Web Services (see \"Limits you should know about\").",
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
      "Deleting removes your records from this app's own database, and only from there. Copies that exist elsewhere, described in the next four points, are not changed by it.",
      "Daily encrypted backups: once a day, an automated job copies the whole database, including accounts and trips, encrypts the copy and stores it with Amazon Web Services (Amazon S3), so that the service can be restored if something goes wrong. The unencrypted copy exists only briefly, on GitHub's servers where the job runs, while it is being encrypted. Each backup is deleted automatically about seven days after it is made. Backups are not edited when you delete a trip or your account, and deleting does not remove them early: something you deleted stays in the backups made before you deleted it until those backups are deleted. To check that restoring works, the operator occasionally restores a backup into a temporary database on their own computer and removes it afterwards.",
      "Database exports that the project operator keeps: separately from the automated backups, before a new version is deployed the operator may export a copy of the database so that it can be restored if something goes wrong. Such an export may be kept temporarily for that purpose. It is not edited when you delete a trip or your account, so something you deleted can remain in an export made before you deleted it, until that export is disposed of. This project has not yet set a retention period for these exports.",
      "Backups and logs kept by the hosting services: deletion does not reach them. They are removed on those services' own schedules, which this project has not verified.",
      "What was already sent to the external services above to build an itinerary cannot be recalled. They keep it according to their own policies.",
      "To keep daily limits fair, the time of a recent itinerary request is kept for up to 24 hours after its trip or account is deleted, without the trip and, once the account is deleted, without any link to you.",
      PRIVACY_PASSWORD_LOSS_POINT,
      `If you cannot sign in and want your account deleted: email ${ACCOUNT_DELETION_CONTACT} and give the email address of the account. You get the same reply whether or not an account uses that address. If one does, a one-time code is sent to that address, and the account is deleted only if that code is sent back within the time the message states (at most ${ACCOUNT_DELETION_CODE_HOURS} hours); a few wrong codes cancel the request. A message that merely comes from the account's address is not accepted as proof on its own. Once the code matches, the account and all its trips are removed from the app's database, exactly as with "Delete account". Copies in the daily encrypted backups stay until those backups are deleted, about seven days later, and a database export kept by the operator is not edited (both described above).`,
      "These deletion requests are handled by hand through a Gmail mailbox, so Google processes those messages. The project keeps only the date and outcome of a request, not your email address, and deletes the correspondence once the request is closed.",
    ],
  },
  {
    heading: "Questions",
    points: [
      "This project has no support desk. To have an account deleted when you cannot sign in, see \"Limits you should know about\" above. For anything else, you can open an issue on the project's public GitHub repository. Issues are public, so do not include personal details there.",
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
