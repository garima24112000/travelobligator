"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import {
  activateBranch,
  ApiRequestError,
  compareRevisions,
  createBranch,
  getRevisionDetail,
  listBranches,
  listBranchRevisions,
} from "@/lib/api";
import type {
  ItineraryBranch,
  ItineraryRevisionComparison,
  ItineraryRevisionDetail,
  ItineraryRevisionSummary,
} from "@/lib/types";

// Section 199C: the user-facing branch workspace. Every piece of state
// shown here is read from the backend's own branch/revision APIs and
// reloaded after every action -- nothing about which branch is active,
// what a branch head is, or what differs between two revisions is ever
// computed or faked client-side. Branch/revision identity is always the
// stable `branch_id`/`revision_id` (version labels repeat across
// branches), and this panel never ranks or recommends a branch.

const FOCUS_RING_CLASSNAME =
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50";

const MAX_BRANCH_NAME_LENGTH = 80;

type ActionError = { code: string; message: string };

type RevisionOption = {
  revision: ItineraryRevisionSummary;
  ownerBranchName: string;
  isHeadOf: string[];
};

function toActionError(err: unknown, fallback: string): ActionError {
  if (err instanceof ApiRequestError) {
    return { code: err.code ?? "UNKNOWN_ERROR", message: err.message };
  }
  return { code: "UNKNOWN_ERROR", message: fallback };
}

function shortId(id: string): string {
  return id.length > 14 ? `${id.slice(0, 14)}…` : id;
}

function creatorLabel(createdBy: string): string {
  if (createdBy === "system_generation") return "Initial plan";
  if (createdBy === "user_feedback") return "Regenerated from feedback";
  return createdBy;
}

function formatWhen(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

// Friendly, factual copy per structured backend refusal. The backend's own
// message stays the fallback so nothing is ever hidden or invented.
function describeActivationError(error: ActionError): string {
  const message = error.message.toLowerCase();
  if (error.code === "BRANCH_SWITCH_BLOCKED") {
    if (message.includes("feedback")) {
      return "Apply or resolve the pending feedback before switching branches.";
    }
    if (message.includes("lock")) {
      return "Unlock the itinerary before switching branches.";
    }
    if (message.includes("job")) {
      return "Wait for the current itinerary job to finish before switching branches.";
    }
    return error.message;
  }
  if (error.code === "BRANCH_STATE_CONFLICT") {
    return "The current itinerary no longer matches its stored branch head. Reload before continuing.";
  }
  if (error.code === "REVISION_SNAPSHOT_UNAVAILABLE") {
    return "This branch has no stored snapshot to switch to.";
  }
  if (error.code === "BRANCH_NOT_FOUND") {
    return "This branch no longer exists for this trip. The branch list has been refreshed.";
  }
  return error.message;
}

function describeCreateError(error: ActionError): string {
  if (error.code === "BRANCH_NAME_CONFLICT") {
    return "A branch with this name already exists for this trip. Choose a different name.";
  }
  if (error.code === "REVISION_SNAPSHOT_UNAVAILABLE") {
    return "Historical snapshot unavailable for this revision, so a branch cannot be created from it.";
  }
  if (error.code === "REVISION_NOT_FOUND") {
    return "That revision no longer exists for this trip. The revision list has been refreshed.";
  }
  return error.message;
}

function ErrorPanel({
  title,
  message,
  panelRef,
}: {
  title: string;
  message: string;
  panelRef: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    <div
      ref={panelRef}
      role="alert"
      tabIndex={-1}
      className={`mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm ${FOCUS_RING_CLASSNAME}`}
    >
      <p className="font-semibold text-red-300">{title}</p>
      <p className="mt-1 break-words text-xs text-red-200">{message}</p>
    </div>
  );
}

function statusLabel(status: string | null): string {
  return status ?? "unknown";
}

async function fetchBranchData(tripId: string) {
  const branchData = await listBranches(tripId);
  const entries = await Promise.all(
    branchData.branches.map(
      async (branch) =>
        [
          branch.branch_id,
          (await listBranchRevisions(tripId, branch.branch_id)).revisions,
        ] as const,
    ),
  );
  return {
    branches: branchData.branches,
    activeBranchId: branchData.active_branch_id,
    revisionsByBranch: Object.fromEntries(entries) as Record<
      string,
      ItineraryRevisionSummary[]
    >,
  };
}

export default function BranchWorkspacePanel({
  tripId,
  refreshKey,
  pendingFeedbackCount,
  activeLockCount,
  isRegenerationRunning,
  onWorkspaceChanged,
  onAuthenticationRequired,
}: {
  tripId: string;
  // Changes whenever the active branch's head may have advanced (a
  // regeneration completed) so this panel never shows stale head labels.
  refreshKey: string;
  pendingFeedbackCount: number;
  activeLockCount: number;
  isRegenerationRunning: boolean;
  // Reloads the persisted trip and clears every piece of state that
  // belonged to the previously displayed workspace.
  onWorkspaceChanged: () => Promise<void>;
  onAuthenticationRequired: () => void;
}) {
  const [branches, setBranches] = useState<ItineraryBranch[] | null>(null);
  const [activeBranchId, setActiveBranchId] = useState<string | null>(null);
  const [revisionsByBranch, setRevisionsByBranch] = useState<
    Record<string, ItineraryRevisionSummary[]>
  >({});
  const [loadError, setLoadError] = useState<ActionError | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [expandedBranchIds, setExpandedBranchIds] = useState<string[]>([]);

  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [sourceRevisionId, setSourceRevisionId] = useState("");
  const [activateAfterCreate, setActivateAfterCreate] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<ActionError | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const [activatingBranchId, setActivatingBranchId] = useState<string | null>(null);
  const [activationError, setActivationError] = useState<ActionError | null>(null);

  const [preview, setPreview] = useState<{
    summary: ItineraryRevisionSummary;
    detail: ItineraryRevisionDetail | null;
    error: ActionError | null;
    isLoading: boolean;
  } | null>(null);

  // `null` = "use the derived default" (active head vs another branch's
  // head); a string (including "") is an explicit user choice.
  const [compareLeftChoice, setCompareLeftChoice] = useState<string | null>(null);
  const [compareRightChoice, setCompareRightChoice] = useState<string | null>(null);
  const [comparison, setComparison] = useState<ItineraryRevisionComparison | null>(null);
  const [comparisonError, setComparisonError] = useState<ActionError | null>(null);
  const [isComparing, setIsComparing] = useState(false);

  const activationErrorRef = useRef<HTMLDivElement | null>(null);
  const createErrorRef = useRef<HTMLDivElement | null>(null);
  const comparisonErrorRef = useRef<HTMLDivElement | null>(null);
  const previewHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const createNameRef = useRef<HTMLInputElement | null>(null);

  const anyBusy = isCreating || activatingBranchId !== null;

  const applyLoaded = useCallback(
    (
      loaded: Awaited<ReturnType<typeof fetchBranchData>>,
    ) => {
      setBranches(loaded.branches);
      setActiveBranchId(loaded.activeBranchId);
      setRevisionsByBranch(loaded.revisionsByBranch);
      setLoadError(null);
      setIsLoading(false);
    },
    [],
  );

  const handleLoadFailure = useCallback(
    (err: unknown) => {
      const error = toActionError(err, "Something went wrong while loading branches.");
      if (error.code === "AUTHENTICATION_REQUIRED") {
        onAuthenticationRequired();
        return;
      }
      setLoadError(error);
      setIsLoading(false);
    },
    [onAuthenticationRequired],
  );

  // Used after user actions; the initial/refresh load below is the same
  // fetch, just cancellable when the trip or refreshKey changes.
  const reload = useCallback(async () => {
    try {
      applyLoaded(await fetchBranchData(tripId));
    } catch (err) {
      handleLoadFailure(err);
    }
  }, [tripId, applyLoaded, handleLoadFailure]);

  useEffect(() => {
    let cancelled = false;
    fetchBranchData(tripId)
      .then((loaded) => {
        if (!cancelled) applyLoaded(loaded);
      })
      .catch((err) => {
        if (!cancelled) handleLoadFailure(err);
      });
    return () => {
      cancelled = true;
    };
  }, [tripId, refreshKey, applyLoaded, handleLoadFailure]);

  useEffect(() => {
    if (activationError) activationErrorRef.current?.focus();
  }, [activationError]);
  useEffect(() => {
    if (createError) createErrorRef.current?.focus();
  }, [createError]);
  useEffect(() => {
    if (comparisonError) comparisonErrorRef.current?.focus();
  }, [comparisonError]);
  useEffect(() => {
    if (preview) previewHeadingRef.current?.focus();
  }, [preview?.summary.revision_id, preview]);

  const activeBranch = branches?.find((b) => b.branch_id === activeBranchId) ?? null;

  // Every real revision the lineage API returned, across all branches --
  // never manufactured from VersionHistoryItem metadata. A revision is
  // listed under the branch that OWNS it (a fresh fork's inherited head
  // therefore appears under its source branch, not the fork).
  const revisionOptions: RevisionOption[] = (branches ?? []).flatMap((branch) =>
    (revisionsByBranch[branch.branch_id] ?? []).map((revision) => ({
      revision,
      ownerBranchName: branch.display_name,
      isHeadOf: (branches ?? [])
        .filter((b) => b.head_revision_id === revision.revision_id)
        .map((b) => b.display_name),
    })),
  );
  const snapshotOptions = revisionOptions.filter((o) => o.revision.snapshot_available);
  const revisionById = new Map(revisionOptions.map((o) => [o.revision.revision_id, o]));

  // Sensible comparison defaults -- the active branch's head vs another
  // branch's head, only ever a real revision the lineage API returned. An
  // explicit choice that no longer exists after a refresh falls back to
  // "nothing selected" rather than showing a stale identity.
  const otherBranchHeadId =
    (branches ?? []).find(
      (b) => b.branch_id !== activeBranchId && b.head_revision_id !== null,
    )?.head_revision_id ?? "";
  const rawLeft = compareLeftChoice ?? activeBranch?.head_revision_id ?? "";
  const rawRight = compareRightChoice ?? otherBranchHeadId;
  const compareLeftId = revisionById.has(rawLeft) ? rawLeft : "";
  const compareRightId = revisionById.has(rawRight) ? rawRight : "";
  const setCompareLeftId = setCompareLeftChoice;
  const setCompareRightId = setCompareRightChoice;
  // A previewed revision that vanished from refreshed branch data is not
  // shown stale.
  const visiblePreview =
    preview && revisionById.has(preview.summary.revision_id) ? preview : null;

  function optionLabel(option: RevisionOption): string {
    const heads =
      option.isHeadOf.length > 0 ? ` (current head of ${option.isHeadOf.join(", ")})` : "";
    return `${option.ownerBranchName} · ${option.revision.version_label}${heads}`;
  }

  function openCreateForm(preselectRevisionId?: string) {
    setIsCreateOpen(true);
    setCreateError(null);
    setNameError(null);
    setStatusMessage(null);
    const defaultSource =
      preselectRevisionId ??
      activeBranch?.head_revision_id ??
      snapshotOptions[0]?.revision.revision_id ??
      "";
    setSourceRevisionId(defaultSource);
    window.setTimeout(() => createNameRef.current?.focus(), 0);
  }

  function clearWorkspaceDependentViews() {
    setPreview(null);
    setComparison(null);
    setComparisonError(null);
    setCompareLeftChoice(null);
    setCompareRightChoice(null);
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    const trimmed = newName.trim();
    if (!trimmed) {
      setNameError("Enter a name for the new branch.");
      createNameRef.current?.focus();
      return;
    }
    if (trimmed.length > MAX_BRANCH_NAME_LENGTH) {
      setNameError(`Use ${MAX_BRANCH_NAME_LENGTH} characters or fewer.`);
      createNameRef.current?.focus();
      return;
    }
    if (!sourceRevisionId) {
      setNameError("Choose a revision to create the branch from.");
      return;
    }
    setNameError(null);
    setCreateError(null);
    setStatusMessage(null);
    setIsCreating(true);
    try {
      const response = await createBranch(tripId, {
        source_revision_id: sourceRevisionId,
        display_name: trimmed,
        activate_after_create: activateAfterCreate,
      });
      setNewName("");
      setIsCreateOpen(false);
      if (response.activated) {
        clearWorkspaceDependentViews();
        await onWorkspaceChanged();
        setStatusMessage(`Branch "${trimmed}" created and now active.`);
      } else if (activateAfterCreate) {
        setStatusMessage(
          `Branch "${trimmed}" was created, but the itinerary was not switched: ${
            response.activation_message ?? "switching was refused."
          } The current itinerary is unchanged.`,
        );
      } else {
        setStatusMessage(
          `Branch "${trimmed}" created. The current itinerary is unchanged.`,
        );
      }
      await reload();
    } catch (err) {
      const error = toActionError(err, "Something went wrong while creating the branch.");
      if (error.code === "AUTHENTICATION_REQUIRED") {
        onAuthenticationRequired();
        return;
      }
      if (error.code === "BRANCH_NAME_CONFLICT") {
        setNameError(describeCreateError(error));
        createNameRef.current?.focus();
      } else {
        setCreateError(error);
      }
      if (error.code === "REVISION_NOT_FOUND" || error.code === "REVISION_SNAPSHOT_UNAVAILABLE") {
        await reload();
      }
    } finally {
      setIsCreating(false);
    }
  }

  async function handleActivate(branch: ItineraryBranch) {
    setActivationError(null);
    setStatusMessage(null);
    setActivatingBranchId(branch.branch_id);
    try {
      await activateBranch(tripId, branch.branch_id);
      clearWorkspaceDependentViews();
      await onWorkspaceChanged();
      await reload();
      setStatusMessage(`Now editing branch "${branch.display_name}".`);
    } catch (err) {
      const error = toActionError(err, "Something went wrong while switching branches.");
      if (error.code === "AUTHENTICATION_REQUIRED") {
        onAuthenticationRequired();
        return;
      }
      setActivationError(error);
      // Refresh metadata only -- for a workspace conflict the backend
      // deliberately refuses to guess which state wins, and this panel
      // never restores a snapshot over the live itinerary itself.
      if (error.code === "BRANCH_STATE_CONFLICT" || error.code === "BRANCH_NOT_FOUND") {
        await reload();
      }
    } finally {
      setActivatingBranchId(null);
    }
  }

  async function handlePreview(summary: ItineraryRevisionSummary) {
    setPreview({ summary, detail: null, error: null, isLoading: true });
    try {
      const detail = await getRevisionDetail(tripId, summary.revision_id);
      setPreview({ summary, detail, error: null, isLoading: false });
    } catch (err) {
      const error = toActionError(err, "Something went wrong while loading this revision.");
      if (error.code === "AUTHENTICATION_REQUIRED") {
        onAuthenticationRequired();
        return;
      }
      setPreview({ summary, detail: null, error, isLoading: false });
    }
  }

  async function handleCompare(event: FormEvent) {
    event.preventDefault();
    if (!compareLeftId || !compareRightId) return;
    setIsComparing(true);
    setComparison(null);
    setComparisonError(null);
    try {
      setComparison(await compareRevisions(tripId, compareLeftId, compareRightId));
    } catch (err) {
      const error = toActionError(err, "Something went wrong while comparing revisions.");
      if (error.code === "AUTHENTICATION_REQUIRED") {
        onAuthenticationRequired();
        return;
      }
      setComparisonError(error);
    } finally {
      setIsComparing(false);
    }
  }

  function toggleExpanded(branchId: string) {
    setExpandedBranchIds((previous) =>
      previous.includes(branchId)
        ? previous.filter((id) => id !== branchId)
        : [...previous, branchId],
    );
  }

  const blockedHints: string[] = [];
  if (pendingFeedbackCount > 0) {
    blockedHints.push(
      "There is pending feedback on the active itinerary. Apply or resolve it before switching branches.",
    );
  }
  if (activeLockCount > 0) {
    blockedHints.push(
      "The active itinerary has locked items. Unlock them before switching branches.",
    );
  }
  if (isRegenerationRunning) {
    blockedHints.push(
      "An itinerary job is running. Wait for it to finish before switching branches.",
    );
  }

  return (
    <section
      id="branches"
      aria-labelledby="branches-heading"
      className="rounded-2xl border border-white/10 bg-white/5 p-4 sm:p-5"
    >
      <h2 id="branches-heading" className="text-lg font-semibold">
        Itinerary branches
      </h2>
      <p className="mt-1 text-xs text-slate-400">
        Each branch is an independent history of this itinerary. Switching
        changes which itinerary is currently loaded for editing; it never
        creates a new version, and creating a branch never changes the
        revision it starts from.
      </p>

      <p className="mt-3 text-sm text-slate-100" data-testid="active-branch-summary">
        {activeBranch ? (
          <>
            <span className="text-slate-400">Editing branch: </span>
            <span className="break-words font-semibold">{activeBranch.display_name}</span>
            {activeBranch.head_version_label && (
              <span className="text-slate-300">
                {" "}
                (head: {activeBranch.head_branch_display_name ?? activeBranch.display_name} ·{" "}
                {activeBranch.head_version_label})
              </span>
            )}
          </>
        ) : isLoading ? (
          "Loading branches..."
        ) : (
          "Active itinerary: unknown"
        )}
      </p>

      <div role="status" aria-live="polite" className="mt-2 text-xs text-emerald-300">
        {statusMessage}
      </div>

      {loadError && (
        <p role="alert" className="mt-3 break-words text-sm text-red-300">
          {loadError.message}
        </p>
      )}

      {blockedHints.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-xs text-amber-200" aria-label="Why switching may be blocked">
          {blockedHints.map((hint) => (
            <li key={hint}>{hint}</li>
          ))}
        </ul>
      )}

      {activationError && (
        <ErrorPanel
          title="Branch was not switched"
          message={describeActivationError(activationError)}
          panelRef={activationErrorRef}
        />
      )}

      <h3 className="mt-5 text-sm font-semibold text-slate-200">Branches</h3>
      {branches && branches.length === 0 && (
        <p className="mt-2 text-sm text-slate-400">No branches recorded yet.</p>
      )}
      <ul className="mt-2 flex flex-col gap-3">
        {(branches ?? []).map((branch) => {
          const revisions = revisionsByBranch[branch.branch_id] ?? [];
          const expanded = expandedBranchIds.includes(branch.branch_id);
          const inherited =
            branch.head_branch_display_name !== null &&
            branch.head_branch_display_name !== branch.display_name;
          const isActivating = activatingBranchId === branch.branch_id;
          return (
            <li
              key={branch.branch_id}
              data-testid="branch-card"
              data-branch-active={branch.is_active ? "true" : "false"}
              className="rounded-xl border border-white/10 bg-slate-900/60 p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                <h4 className="min-w-0 break-words text-sm font-semibold text-slate-100">
                  {branch.display_name}
                </h4>
                {branch.is_default && (
                  <span className="rounded-full border border-white/15 px-2 py-0.5 text-[11px] text-slate-300">
                    Main (default)
                  </span>
                )}
                {branch.is_active ? (
                  <span className="rounded-full border border-emerald-400/40 bg-emerald-400/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-200">
                    Active — currently loaded for editing
                  </span>
                ) : (
                  <span className="text-[11px] text-slate-500">Not active</span>
                )}
              </div>

              <dl className="mt-2 grid grid-cols-1 gap-1 text-xs text-slate-300 sm:grid-cols-2">
                <div>
                  <dt className="text-slate-500">Head</dt>
                  <dd className="break-words">
                    {branch.head_version_label
                      ? `${branch.head_branch_display_name ?? branch.display_name} · ${branch.head_version_label}`
                      : "No revision recorded yet"}
                    {inherited &&
                      ` (inherited from ${branch.head_branch_display_name}; no revisions of its own yet)`}
                  </dd>
                </div>
                {!branch.is_default && branch.base_revision_id && (
                  <div>
                    <dt className="text-slate-500">Lineage</dt>
                    <dd className="break-words">
                      {branch.base_branch_display_name && branch.base_version_label
                        ? `Created from ${branch.base_branch_display_name} · ${branch.base_version_label}`
                        : `Created from revision ${shortId(branch.base_revision_id)}`}
                    </dd>
                  </div>
                )}
              </dl>

              <div className="mt-3 flex flex-wrap gap-2">
                {!branch.is_active && (
                  <button
                    type="button"
                    onClick={() => void handleActivate(branch)}
                    disabled={anyBusy || isRegenerationRunning}
                    className={`rounded-lg border border-cyan-300/40 bg-slate-900 px-3 py-1.5 text-xs font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
                  >
                    {isActivating ? "Switching..." : `Switch to ${branch.display_name}`}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => toggleExpanded(branch.branch_id)}
                  aria-expanded={expanded}
                  aria-controls={`revisions-${branch.branch_id}`}
                  className={`rounded-lg border border-white/15 px-3 py-1.5 text-xs text-slate-200 transition hover:bg-slate-800 ${FOCUS_RING_CLASSNAME}`}
                >
                  {expanded ? "Hide revisions" : "Show revisions"}
                </button>
              </div>

              {expanded && (
                <div id={`revisions-${branch.branch_id}`} className="mt-3">
                  <h5 className="text-xs font-semibold text-slate-300">
                    Revisions of {branch.display_name}
                  </h5>
                  {!branch.is_default && branch.base_revision_id && (
                    <p className="mt-1 break-words text-[11px] text-slate-500">
                      Starts from{" "}
                      {branch.base_branch_display_name && branch.base_version_label
                        ? `${branch.base_branch_display_name} · ${branch.base_version_label}`
                        : shortId(branch.base_revision_id)}{" "}
                      (inherited — listed under its own branch, not here).
                    </p>
                  )}
                  {revisions.length === 0 ? (
                    <p className="mt-2 text-xs text-slate-400">
                      No revisions of its own yet.
                      {branch.head_version_label &&
                        ` This branch currently points at ${
                          branch.head_branch_display_name ?? branch.display_name
                        } · ${branch.head_version_label}.`}
                    </p>
                  ) : (
                    <ol className="mt-2 flex flex-col gap-2">
                      {revisions.map((revision) => {
                        const isHead = revision.revision_id === branch.head_revision_id;
                        return (
                          <li
                            key={revision.revision_id}
                            data-testid="revision-row"
                            className="rounded-lg border border-white/10 bg-slate-950/50 p-3 text-xs"
                          >
                            <p className="flex flex-wrap items-center gap-2">
                              <span className="font-semibold text-slate-100">
                                {branch.display_name} · {revision.version_label}
                              </span>
                              {isHead && (
                                <span className="rounded-full border border-cyan-300/40 px-2 py-0.5 text-[11px] text-cyan-200">
                                  Head
                                </span>
                              )}
                            </p>
                            <p className="mt-1 break-words text-slate-400">
                              {creatorLabel(revision.created_by)} · {formatWhen(revision.created_at)}
                            </p>
                            {revision.snapshot_available ? (
                              <div className="mt-2 flex flex-wrap gap-2">
                                <button
                                  type="button"
                                  onClick={() => void handlePreview(revision)}
                                  className={`rounded-lg border border-white/15 px-2.5 py-1 text-slate-200 transition hover:bg-slate-800 ${FOCUS_RING_CLASSNAME}`}
                                >
                                  View read-only
                                </button>
                                <button
                                  type="button"
                                  onClick={() => openCreateForm(revision.revision_id)}
                                  disabled={anyBusy}
                                  className={`rounded-lg border border-white/15 px-2.5 py-1 text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
                                >
                                  Create branch from this revision
                                </button>
                              </div>
                            ) : (
                              <p className="mt-2 text-slate-500">
                                Historical snapshot unavailable for this revision.
                              </p>
                            )}
                          </li>
                        );
                      })}
                    </ol>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <div className="mt-4">
        {!isCreateOpen && (
          <button
            type="button"
            onClick={() => openCreateForm()}
            disabled={anyBusy || snapshotOptions.length === 0}
            className={`rounded-lg border border-cyan-300/40 bg-slate-900 px-4 py-2 text-sm font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
          >
            Create branch (alternate itinerary from a revision)
          </button>
        )}
        {snapshotOptions.length === 0 && !isLoading && (
          <p className="mt-2 text-xs text-slate-500">
            A branch can only be created from a revision with a stored
            snapshot. None is available for this trip yet.
          </p>
        )}
      </div>

      {isCreateOpen && (
        <form
          onSubmit={(event) => void handleCreate(event)}
          aria-labelledby="create-branch-heading"
          className="mt-3 rounded-xl border border-white/10 bg-slate-900/60 p-4"
        >
          <h3 id="create-branch-heading" className="text-sm font-semibold text-slate-100">
            Create an alternate itinerary from a revision
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            The new branch (a &ldquo;fork&rdquo;) starts as a copy of the
            revision you choose. That revision itself is never changed. Only
            revisions with a stored snapshot can be used.
          </p>

          <label htmlFor="new-branch-name" className="mt-3 block text-xs text-slate-300">
            Branch name
          </label>
          <input
            id="new-branch-name"
            ref={createNameRef}
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            maxLength={MAX_BRANCH_NAME_LENGTH}
            aria-invalid={nameError !== null}
            aria-describedby={nameError ? "new-branch-name-error" : undefined}
            className={`mt-1 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 ${FOCUS_RING_CLASSNAME}`}
          />
          {nameError && (
            <p id="new-branch-name-error" role="alert" className="mt-1 break-words text-xs text-red-300">
              {nameError}
            </p>
          )}

          <label htmlFor="new-branch-source" className="mt-3 block text-xs text-slate-300">
            Create from revision
          </label>
          <select
            id="new-branch-source"
            value={sourceRevisionId}
            onChange={(event) => setSourceRevisionId(event.target.value)}
            className={`mt-1 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 ${FOCUS_RING_CLASSNAME}`}
          >
            {revisionOptions.map((option) => (
              <option
                key={option.revision.revision_id}
                value={option.revision.revision_id}
                disabled={!option.revision.snapshot_available}
              >
                {optionLabel(option)}
                {option.revision.snapshot_available
                  ? ""
                  : " — historical snapshot unavailable"}
              </option>
            ))}
          </select>

          <label className="mt-3 flex items-start gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={activateAfterCreate}
              onChange={(event) => setActivateAfterCreate(event.target.checked)}
              className={`mt-0.5 ${FOCUS_RING_CLASSNAME}`}
            />
            <span>Switch to this branch after creating it</span>
          </label>

          {createError && (
            <ErrorPanel
              title="Branch was not created"
              message={describeCreateError(createError)}
              panelRef={createErrorRef}
            />
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={isCreating}
              className={`rounded-lg border border-cyan-300/40 bg-slate-900 px-4 py-2 text-sm font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
            >
              {isCreating ? "Creating branch..." : "Create branch"}
            </button>
            <button
              type="button"
              onClick={() => {
                setIsCreateOpen(false);
                setCreateError(null);
                setNameError(null);
              }}
              disabled={isCreating}
              className={`rounded-lg border border-white/15 px-4 py-2 text-sm text-slate-200 transition hover:bg-slate-800 ${FOCUS_RING_CLASSNAME}`}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {visiblePreview && (
        <section
          aria-labelledby="revision-preview-heading"
          data-testid="historical-preview"
          className="mt-5 rounded-xl border border-amber-400/30 bg-amber-400/5 p-4"
        >
          <h3
            id="revision-preview-heading"
            ref={previewHeadingRef}
            tabIndex={-1}
            className={`text-sm font-semibold text-amber-200 ${FOCUS_RING_CLASSNAME}`}
          >
            Historical revision — read only
          </h3>
          <p className="mt-1 break-words text-xs text-slate-300">
            {revisionById.get(visiblePreview.summary.revision_id)?.ownerBranchName ?? "Branch"} ·{" "}
            {visiblePreview.summary.version_label} · {formatWhen(visiblePreview.summary.created_at)}. This is a
            view only: feedback, locks and regeneration apply to the active itinerary, not to this
            revision, and the active itinerary is unchanged.
          </p>
          {visiblePreview.isLoading && <p className="mt-2 text-xs text-slate-400">Loading revision...</p>}
          {visiblePreview.error && (
            <p role="alert" className="mt-2 break-words text-xs text-red-300">
              {describeCreateError(visiblePreview.error)}
            </p>
          )}
          {visiblePreview.detail && !visiblePreview.detail.planning_state && (
            <p className="mt-2 text-xs text-slate-400">
              Historical snapshot unavailable for this revision.
            </p>
          )}
          {visiblePreview.detail?.planning_state?.experience_plan && (
            <div className="mt-3 flex flex-col gap-3">
              {visiblePreview.detail.planning_state.experience_plan.daily_plans.map((day) => (
                <div key={day.day_plan_id} className="rounded-lg border border-white/10 bg-slate-950/50 p-3">
                  <h4 className="text-xs font-semibold text-slate-200">
                    Day {day.day_number} · {day.date}
                  </h4>
                  {day.experiences.length === 0 ? (
                    <p className="mt-1 text-xs text-slate-500">No scheduled places.</p>
                  ) : (
                    <ol className="mt-1 list-decimal pl-5 text-xs text-slate-300">
                      {day.experiences.map((experience) => (
                        <li key={experience.experience_id} className="break-words">
                          {experience.name}{" "}
                          <span className="text-slate-500">({experience.category})</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={() => setPreview(null)}
            className={`mt-3 rounded-lg border border-white/15 px-3 py-1.5 text-xs text-slate-200 transition hover:bg-slate-800 ${FOCUS_RING_CLASSNAME}`}
          >
            Back to active itinerary
          </button>
        </section>
      )}

      <section aria-labelledby="compare-heading" className="mt-6">
        <h3 id="compare-heading" className="text-sm font-semibold text-slate-200">
          Compare two revisions
        </h3>
        <p className="mt-1 text-xs text-slate-400">
          A factual, deterministic comparison of itinerary items, day order,
          pace/interests, and validation status and counts. It does not rank
          or recommend either revision.
        </p>
        {snapshotOptions.length < 2 ? (
          <p className="mt-2 text-xs text-slate-500">
            At least two revisions with stored snapshots are needed to compare.
          </p>
        ) : (
          <form onSubmit={(event) => void handleCompare(event)} className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="compare-left" className="block text-xs text-slate-300">
                Left revision
              </label>
              <select
                id="compare-left"
                value={compareLeftId}
                onChange={(event) => setCompareLeftId(event.target.value)}
                className={`mt-1 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 ${FOCUS_RING_CLASSNAME}`}
              >
                <option value="">Choose a revision</option>
                {snapshotOptions.map((option) => (
                  <option key={option.revision.revision_id} value={option.revision.revision_id}>
                    {optionLabel(option)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="compare-right" className="block text-xs text-slate-300">
                Right revision
              </label>
              <select
                id="compare-right"
                value={compareRightId}
                onChange={(event) => setCompareRightId(event.target.value)}
                className={`mt-1 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 ${FOCUS_RING_CLASSNAME}`}
              >
                <option value="">Choose a revision</option>
                {snapshotOptions.map((option) => (
                  <option key={option.revision.revision_id} value={option.revision.revision_id}>
                    {optionLabel(option)}
                  </option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-2">
              <button
                type="submit"
                disabled={isComparing || !compareLeftId || !compareRightId}
                className={`rounded-lg border border-cyan-300/40 bg-slate-900 px-4 py-2 text-sm font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
              >
                {isComparing ? "Comparing..." : "Compare revisions"}
              </button>
            </div>
          </form>
        )}

        {comparisonError && (
          <ErrorPanel
            title="Revisions were not compared"
            message={describeCreateError(comparisonError)}
            panelRef={comparisonErrorRef}
          />
        )}

        {comparison && (
          <div
            role="region"
            aria-label="Revision comparison result"
            data-testid="comparison-result"
            className="mt-4 rounded-xl border border-white/10 bg-slate-900/60 p-4 text-xs text-slate-200"
          >
            <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div>
                <dt className="text-slate-500">Left</dt>
                <dd className="break-words font-semibold">
                  {comparison.left.branch_display_name ?? "Branch"} · {comparison.left.version_label}
                  <span className="ml-1 font-normal text-slate-500">
                    ({shortId(comparison.left.revision_id)})
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">Right</dt>
                <dd className="break-words font-semibold">
                  {comparison.right.branch_display_name ?? "Branch"} · {comparison.right.version_label}
                  <span className="ml-1 font-normal text-slate-500">
                    ({shortId(comparison.right.revision_id)})
                  </span>
                </dd>
              </div>
            </dl>

            {comparison.no_compared_differences ? (
              <p className="mt-3">
                No itinerary-content differences found between these revisions.
                <span className="block text-slate-500">
                  Compared: itinerary items, day order, pace and interests, and
                  validation status and counts. Other stored details are not compared.
                </span>
              </p>
            ) : (
              <div className="mt-3 flex flex-col gap-3">
                {comparison.added_experiences.length > 0 && (
                  <div>
                    <h4 className="font-semibold">Added (in right, not in left)</h4>
                    <ul className="mt-1 list-disc break-words pl-5">
                      {comparison.added_experiences.map((item) => (
                        <li key={item.experience_id}>{item.name}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {comparison.removed_experiences.length > 0 && (
                  <div>
                    <h4 className="font-semibold">Removed (in left, not in right)</h4>
                    <ul className="mt-1 list-disc break-words pl-5">
                      {comparison.removed_experiences.map((item) => (
                        <li key={item.experience_id}>{item.name}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {comparison.moved_experiences.length > 0 && (
                  <div>
                    <h4 className="font-semibold">Moved (left → right)</h4>
                    <ul className="mt-1 list-disc break-words pl-5">
                      {comparison.moved_experiences.map((item) => (
                        <li key={item.experience_id}>
                          {item.name}: Day {item.from_day} → Day {item.to_day}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {comparison.reordered_days.length > 0 && (
                  <div>
                    <h4 className="font-semibold">Order changed (left → right)</h4>
                    <ul className="mt-1 list-disc break-words pl-5">
                      {comparison.reordered_days.map((day) => (
                        <li key={day.day_index}>
                          Day {day.day_index}: {day.before_order.map((e) => e.name).join(", ")} →{" "}
                          {day.after_order.map((e) => e.name).join(", ")}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {comparison.traveler_profile_diff && (
                  <div>
                    <h4 className="font-semibold">Pace and preferences</h4>
                    <ul className="mt-1 list-disc break-words pl-5">
                      {comparison.traveler_profile_diff.pace_before !==
                        comparison.traveler_profile_diff.pace_after && (
                        <li>
                          Pace: {comparison.traveler_profile_diff.pace_before ?? "unset"} →{" "}
                          {comparison.traveler_profile_diff.pace_after ?? "unset"}
                        </li>
                      )}
                      {comparison.traveler_profile_diff.interests_added.length > 0 && (
                        <li>
                          Interests only in right:{" "}
                          {comparison.traveler_profile_diff.interests_added.join(", ")}
                        </li>
                      )}
                      {comparison.traveler_profile_diff.interests_removed.length > 0 && (
                        <li>
                          Interests only in left:{" "}
                          {comparison.traveler_profile_diff.interests_removed.join(", ")}
                        </li>
                      )}
                    </ul>
                  </div>
                )}
              </div>
            )}

            <div className="mt-3">
              <h4 className="font-semibold">Validation</h4>
              <ul className="mt-1 list-disc break-words pl-5">
                <li>
                  Left: {statusLabel(comparison.validation_status_left)} —{" "}
                  {comparison.warning_count_left} warning
                  {comparison.warning_count_left === 1 ? "" : "s"},{" "}
                  {comparison.critical_issue_count_left} critical issue
                  {comparison.critical_issue_count_left === 1 ? "" : "s"}
                </li>
                <li>
                  Right: {statusLabel(comparison.validation_status_right)} —{" "}
                  {comparison.warning_count_right} warning
                  {comparison.warning_count_right === 1 ? "" : "s"},{" "}
                  {comparison.critical_issue_count_right} critical issue
                  {comparison.critical_issue_count_right === 1 ? "" : "s"}
                </li>
              </ul>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
