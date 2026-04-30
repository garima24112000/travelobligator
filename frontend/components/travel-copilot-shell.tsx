import type {
  AccommodationRecommendation,
  Itinerary,
  ItineraryDay,
  TransportPreference,
  TravelGroupType,
  TripPace,
} from "../../shared/types";

type QuickAction = {
  label: string;
  description: string;
};

type TravelCopilotShellProps = {
  itinerary: Itinerary;
  preferences: {
    destination: string;
    dates: string;
    travelers: string;
    groupType: TravelGroupType;
    pace: TripPace;
    accommodationType: string;
    transportPreference: TransportPreference;
    budget: string;
    interests: string[];
    mustVisit: string[];
    mustAvoid: string[];
    notes: string;
  };
  accommodationRecommendations: AccommodationRecommendation[];
  transportRecommendations: string[];
  quickActions: QuickAction[];
};

const travelGroupLabels: Record<TravelGroupType, string> = {
  solo: "Solo",
  couple: "Couple",
  family: "Family",
  friends: "Friends",
  group: "Group",
};

const paceLabels: Record<TripPace, string> = {
  relaxed: "Relaxed",
  balanced: "Balanced",
  packed: "Packed",
};

const transportLabels: Record<TransportPreference, string> = {
  public_transport: "Public transport",
  taxi: "Taxi / rideshare",
  self_drive: "Self drive",
  train: "Train",
  flight: "Flight",
  no_preference: "No preference",
};

function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-[0.35em] text-amber-200/80">
        {eyebrow}
      </p>
      <h2 className="text-2xl font-semibold text-slate-50 sm:text-3xl">
        {title}
      </h2>
      <p className="max-w-3xl text-sm leading-6 text-slate-300">
        {description}
      </p>
    </div>
  );
}

function LabelValueCard({
  label,
  value,
  helper,
}: {
  label: string;
  value: string;
  helper?: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
      <p className="text-xs uppercase tracking-[0.28em] text-slate-400">
        {label}
      </p>
      <p className="mt-2 text-sm font-medium text-slate-100">{value}</p>
      {helper ? <p className="mt-1 text-xs text-slate-400">{helper}</p> : null}
    </div>
  );
}

function TagList({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span
          key={item}
          className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-slate-200"
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function PreferenceRow({
  label,
  value,
  helper,
}: {
  label: string;
  value: string;
  helper?: string;
}) {
  return (
    <label className="space-y-2">
      <span className="text-sm font-medium text-slate-200">{label}</span>
      <input
        readOnly
        defaultValue={value}
        className="w-full rounded-2xl border border-white/10 bg-slate-950/55 px-4 py-3 text-sm text-slate-100 outline-none transition focus:border-amber-300/60 focus:ring-2 focus:ring-amber-300/20"
      />
      {helper ? <p className="text-xs text-slate-400">{helper}</p> : null}
    </label>
  );
}

function DayCard({ day }: { day: ItineraryDay }) {
  return (
    <article className="rounded-3xl border border-white/10 bg-slate-950/60 p-5 shadow-[0_30px_80px_rgba(2,6,23,0.3)] backdrop-blur">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-amber-200/80">
            Day {day.dayNumber}
          </p>
          <h3 className="mt-1 text-lg font-semibold text-slate-50">
            {day.theme}
          </h3>
          <p className="mt-1 text-sm text-slate-400">Base city: {day.baseCity}</p>
        </div>
        <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-right">
          <p className="text-[11px] uppercase tracking-[0.22em] text-emerald-200">
            Demo estimate
          </p>
          <p className="text-sm font-semibold text-emerald-100">
            ${day.estimatedCost.toLocaleString()}
          </p>
        </div>
      </div>

      <div className="mt-5 space-y-3">
        {day.activities.map((activity, index) => (
          <div
            key={activity.id}
            className="rounded-2xl border border-white/8 bg-white/5 p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-50">
                  {activity.startTime} - {activity.endTime}
                </p>
                <p className="text-sm text-slate-200">{activity.name}</p>
              </div>
              <span className="rounded-full bg-sky-400/10 px-2.5 py-1 text-xs font-medium text-sky-100">
                {activity.category}
              </span>
            </div>
            <p className="mt-2 text-sm leading-6 text-slate-400">
              {activity.whyIncluded}
            </p>
            {activity.travelFromPrevious ? (
              <p className="mt-3 text-xs text-slate-500">
                Travel: {activity.travelFromPrevious.mode} · {activity.travelFromPrevious.timeMinutes} min
                {activity.travelFromPrevious.distanceKm ? ` · ${activity.travelFromPrevious.distanceKm} km` : ""}
              </p>
            ) : null}
            {index === 0 ? (
              <p className="mt-3 inline-flex rounded-full bg-amber-400/10 px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.22em] text-amber-100">
                Mock itinerary data
              </p>
            ) : null}
          </div>
        ))}
      </div>

      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-xs uppercase tracking-[0.26em] text-slate-500">
            Food suggestions
          </p>
          <ul className="mt-2 space-y-2 text-sm text-slate-300">
            {day.foodSuggestions.map((food) => (
              <li key={food} className="rounded-2xl bg-white/5 px-3 py-2">
                {food}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-xs uppercase tracking-[0.26em] text-slate-500">
            Notes
          </p>
          <ul className="mt-2 space-y-2 text-sm text-slate-300">
            {day.notes.map((note) => (
              <li key={note} className="rounded-2xl bg-white/5 px-3 py-2">
                {note}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </article>
  );
}

function RecommendationPanel({
  title,
  subtitle,
  items,
}: {
  title: string;
  subtitle: string;
  items: AccommodationRecommendation[];
}) {
  return (
    <section className="rounded-[2rem] border border-white/10 bg-slate-950/55 p-6 shadow-[0_30px_80px_rgba(2,6,23,0.25)] backdrop-blur">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-slate-50">{title}</h3>
          <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
        </div>
        <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-slate-300">
          Demo recommendations
        </span>
      </div>

      <div className="mt-5 space-y-3">
        {items.map((item) => (
          <article
            key={item.id}
            className="rounded-2xl border border-white/8 bg-white/5 p-4"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h4 className="text-sm font-semibold text-slate-50">{item.name}</h4>
                <p className="mt-1 text-sm text-slate-400">
                  {item.neighborhood} · {item.rating.toFixed(1)} / 5
                </p>
              </div>
              <div className="text-right">
                <p className="text-sm font-semibold text-slate-100">
                  {item.currency} {item.nightlyPrice}
                </p>
                <p className="text-xs text-slate-500">per night</p>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {item.amenities.map((amenity) => (
                <span
                  key={amenity}
                  className="rounded-full bg-sky-400/10 px-2.5 py-1 text-[11px] font-medium text-sky-100"
                >
                  {amenity}
                </span>
              ))}
            </div>
            <ul className="mt-3 space-y-1 text-sm text-slate-400">
              {item.reasons.map((reason) => (
                <li key={reason}>• {reason}</li>
              ))}
            </ul>
            <p className="mt-3 text-xs uppercase tracking-[0.24em] text-amber-200/80">
              Mock data
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}

function TransportPanel({ items }: { items: string[] }) {
  return (
    <section className="rounded-[2rem] border border-white/10 bg-slate-950/55 p-6 shadow-[0_30px_80px_rgba(2,6,23,0.25)] backdrop-blur">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-slate-50">Transport plan</h3>
          <p className="mt-1 text-sm text-slate-400">
            Placeholder transport guidance for the selected destination.
          </p>
        </div>
        <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-slate-300">
          Demo only
        </span>
      </div>
      <div className="mt-5 grid gap-3">
        {items.map((item, index) => (
          <div
            key={item}
            className="rounded-2xl border border-white/8 bg-white/5 p-4"
          >
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-amber-400/10 text-sm font-semibold text-amber-100">
                {index + 1}
              </span>
              <p className="text-sm text-slate-200">{item}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function MapPanel() {
  return (
    <section className="rounded-[2rem] border border-white/10 bg-slate-950/55 p-6 shadow-[0_30px_80px_rgba(2,6,23,0.25)] backdrop-blur">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-slate-50">Map preview</h3>
          <p className="mt-1 text-sm text-slate-400">
            Placeholder route visualization for the itinerary shell.
          </p>
        </div>
        <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-slate-300">
          Mock map panel
        </span>
      </div>
      <div className="mt-5 overflow-hidden rounded-[1.75rem] border border-white/10 bg-[radial-gradient(circle_at_top,_rgba(251,191,36,0.16),_transparent_34%),linear-gradient(135deg,_rgba(15,23,42,0.96),_rgba(2,6,23,0.9))] p-4">
        <div className="relative aspect-[16/11] rounded-[1.5rem] border border-white/10 bg-[linear-gradient(rgba(255,255,255,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.05)_1px,transparent_1px)] bg-[size:24px_24px]">
          <div className="absolute left-[22%] top-[20%] h-4 w-4 rounded-full bg-amber-300 shadow-[0_0_0_10px_rgba(251,191,36,0.16)]" />
          <div className="absolute left-[48%] top-[44%] h-4 w-4 rounded-full bg-sky-300 shadow-[0_0_0_10px_rgba(56,189,248,0.16)]" />
          <div className="absolute left-[72%] top-[66%] h-4 w-4 rounded-full bg-emerald-300 shadow-[0_0_0_10px_rgba(52,211,153,0.14)]" />
          <svg
            viewBox="0 0 100 100"
            className="absolute inset-0 h-full w-full"
            aria-hidden="true"
          >
            <path
              d="M22 20 C34 24, 40 32, 48 44 S66 64, 72 66"
              fill="none"
              stroke="rgba(251,191,36,0.9)"
              strokeWidth="1.5"
              strokeDasharray="3 3"
            />
          </svg>
          <div className="absolute bottom-4 left-4 right-4 grid gap-3 sm:grid-cols-3">
            <div className="rounded-2xl border border-white/10 bg-slate-950/80 px-3 py-2 text-xs text-slate-200">
              Walkable zones highlighted
            </div>
            <div className="rounded-2xl border border-white/10 bg-slate-950/80 px-3 py-2 text-xs text-slate-200">
              Transit corridors indicated
            </div>
            <div className="rounded-2xl border border-white/10 bg-slate-950/80 px-3 py-2 text-xs text-slate-200">
              Demo route overlay only
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export function TravelCopilotShell({
  itinerary,
  preferences,
  accommodationRecommendations,
  transportRecommendations,
  quickActions,
}: TravelCopilotShellProps) {
  return (
    <main className="min-h-screen bg-[#07111f] text-slate-100">
      <div className="absolute inset-0 -z-10 overflow-hidden">
        <div className="absolute left-1/2 top-[-10rem] h-[30rem] w-[30rem] -translate-x-1/2 rounded-full bg-amber-400/12 blur-3xl" />
        <div className="absolute right-[-8rem] top-[18rem] h-[24rem] w-[24rem] rounded-full bg-cyan-400/10 blur-3xl" />
        <div className="absolute bottom-[-10rem] left-[-8rem] h-[24rem] w-[24rem] rounded-full bg-emerald-400/10 blur-3xl" />
      </div>

      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <div className="rounded-[2.5rem] border border-white/10 bg-white/5 p-6 shadow-[0_40px_120px_rgba(2,6,23,0.45)] backdrop-blur-xl sm:p-8 lg:p-10">
          <header className="grid gap-8 lg:grid-cols-[1.3fr_0.7fr] lg:items-start">
            <div className="space-y-6">
              <div className="inline-flex items-center gap-2 rounded-full border border-amber-300/20 bg-amber-300/10 px-3 py-1 text-xs font-medium uppercase tracking-[0.24em] text-amber-100">
                TravelObligator
              </div>
              <div className="space-y-4">
                <h1 className="max-w-4xl text-4xl font-semibold tracking-tight text-slate-50 sm:text-5xl lg:text-6xl">
                  Plan smarter trips with route-aware itinerary guidance, mock recommendations, and quick refinement controls.
                </h1>
                <p className="max-w-3xl text-sm leading-7 text-slate-300 sm:text-base">
                  This dashboard is intentionally demo-only. It shows the shape of the TravelObligator experience while the real providers and itinerary engine are still being built.
                </p>
              </div>

              <div className="grid gap-4 md:grid-cols-3">
                <LabelValueCard label="Destination" value={itinerary.tripSummary.destination} helper={itinerary.tripSummary.summaryText} />
                <LabelValueCard label="Duration" value={`${itinerary.tripSummary.durationDays} days`} helper={`Version ${itinerary.versionNumber} itinerary`} />
                <LabelValueCard label="Budget" value={`${itinerary.estimatedBudgetBreakdown.currency} ${itinerary.estimatedBudgetBreakdown.total.toLocaleString()}`} helper="Mock budget breakdown" />
              </div>
            </div>

            <aside className="rounded-[2rem] border border-white/10 bg-slate-950/50 p-5">
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
                Current trip summary
              </p>
              <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-1">
                <div>
                  <dt className="text-xs uppercase tracking-[0.24em] text-slate-500">Travel style</dt>
                  <dd className="mt-1 text-sm font-medium text-slate-100">{itinerary.tripSummary.travelStyle}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-[0.24em] text-slate-500">Stay area</dt>
                  <dd className="mt-1 text-sm font-medium text-slate-100">{itinerary.stayRecommendation.recommendedArea}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-[0.24em] text-slate-500">Mock status</dt>
                  <dd className="mt-1 text-sm font-medium text-emerald-200">{itinerary.isMockData ? "Demo itinerary data" : "Live data"}</dd>
                </div>
              </dl>
            </aside>
          </header>

          <section className="mt-10 grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
            <div className="space-y-6">
              <div className="rounded-[2rem] border border-white/10 bg-slate-950/55 p-6">
                <SectionHeading
                  eyebrow="Trip preferences"
                  title="Preference capture"
                  description="A polished MVP shell for capturing trip intent. The controls below are read-only placeholders for now, but they match the structured traveler profile the backend will eventually use."
                />
                <div className="mt-6 grid gap-4 md:grid-cols-2">
                  <PreferenceRow label="Destination" value={preferences.destination} />
                  <PreferenceRow label="Dates" value={preferences.dates} />
                  <PreferenceRow label="Travelers" value={preferences.travelers} helper={travelGroupLabels[preferences.groupType]} />
                  <PreferenceRow label="Pace" value={paceLabels[preferences.pace]} helper="Balanced between sightseeing and downtime" />
                  <PreferenceRow label="Accommodation" value={preferences.accommodationType} />
                  <PreferenceRow label="Transport preference" value={transportLabels[preferences.transportPreference]} />
                  <PreferenceRow label="Budget" value={preferences.budget} />
                  <PreferenceRow label="Notes" value={preferences.notes} />
                </div>

                <div className="mt-6 grid gap-6 lg:grid-cols-3">
                  <div>
                    <p className="text-xs uppercase tracking-[0.26em] text-slate-500">Interests</p>
                    <div className="mt-3">
                      <TagList items={preferences.interests} />
                    </div>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-[0.26em] text-slate-500">Must visit</p>
                    <div className="mt-3">
                      <TagList items={preferences.mustVisit} />
                    </div>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-[0.26em] text-slate-500">Must avoid</p>
                    <div className="mt-3">
                      <TagList items={preferences.mustAvoid} />
                    </div>
                  </div>
                </div>
              </div>

              <div className="rounded-[2rem] border border-white/10 bg-slate-950/55 p-6">
                <SectionHeading
                  eyebrow="Itinerary preview"
                  title="Day-wise plan"
                  description="Placeholder itinerary cards show the intended structured output: daily themes, activities, transit context, and rough budget estimates."
                />
                <div className="mt-6 grid gap-5">
                  {itinerary.dailyPlan.map((day) => (
                    <DayCard key={day.dayNumber} day={day} />
                  ))}
                </div>
              </div>
            </div>

            <div className="space-y-6">
              <MapPanel />
              <RecommendationPanel
                title="Accommodation recommendations"
                subtitle="Placeholder stay options ranked for the current trip profile."
                items={accommodationRecommendations}
              />
              <TransportPanel items={transportRecommendations} />

              <section className="rounded-[2rem] border border-white/10 bg-slate-950/55 p-6 shadow-[0_30px_80px_rgba(2,6,23,0.25)] backdrop-blur">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h3 className="text-lg font-semibold text-slate-50">Feedback and refinement</h3>
                    <p className="mt-1 text-sm text-slate-400">
                      Quick actions are wired as static controls for the MVP shell.
                    </p>
                  </div>
                  <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-slate-300">
                    Demo actions
                  </span>
                </div>

                <div className="mt-5 grid gap-3 sm:grid-cols-2">
                  {quickActions.map((action) => (
                    <button
                      key={action.label}
                      type="button"
                      className="rounded-2xl border border-white/10 bg-white/5 px-4 py-4 text-left transition hover:-translate-y-0.5 hover:border-amber-300/40 hover:bg-amber-300/10"
                    >
                      <p className="text-sm font-semibold text-slate-50">{action.label}</p>
                      <p className="mt-1 text-xs leading-5 text-slate-400">{action.description}</p>
                    </button>
                  ))}
                </div>

                <div className="mt-5 rounded-2xl border border-white/10 bg-slate-950/60 p-4">
                  <p className="text-xs uppercase tracking-[0.26em] text-slate-500">Important tips</p>
                  <ul className="mt-3 space-y-2 text-sm text-slate-300">
                    {itinerary.importantTips.map((tip) => (
                      <li key={tip}>• {tip}</li>
                    ))}
                  </ul>
                </div>
              </section>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}