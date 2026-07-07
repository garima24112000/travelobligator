export default function Home() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-100">
      <section className="mx-auto max-w-4xl rounded-3xl border border-white/10 bg-white/5 p-8 shadow-2xl">
        <p className="text-sm font-semibold uppercase tracking-[0.3em] text-cyan-200">
          TravelObligator
        </p>

        <h1 className="mt-4 text-4xl font-semibold tracking-tight sm:text-5xl">
          AI Travel Decision Platform
        </h1>

        <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
          The old mock itinerary shell has been removed. The app is now ready
          for the real PlanningState-based implementation.
        </p>

        <div className="mt-8 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-5 text-sm text-cyan-50">
          <p className="font-semibold">Current status</p>
          <p className="mt-2 leading-6">
            Environment and architecture setup are in progress. No mock travel
            facts, fake hotels, fake prices, fake ratings, or fake itineraries
            are being shown.
          </p>
        </div>
      </section>
    </main>
  );
}