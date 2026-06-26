"use client";

import type { FormEvent, ReactNode } from "react";

import type {
  AccommodationType,
  TransportPreference,
  TravelGroupType,
  TripPace,
  TripRequest,
} from "../../shared/types";

export type TripRequestFormState = {
  destination: string;
  originCity: string;
  startDate: string;
  endDate: string;
  travelersCount: string;
  travelGroupType: TravelGroupType;
  budgetMin: string;
  budgetMax: string;
  pace: TripPace;
  accommodationType: AccommodationType;
  transportPreference: TransportPreference;
  interestsText: string;
  mustVisitText: string;
  mustAvoidText: string;
  constraintsText: string;
  freeTextPreferences: string;
};

type TripPreferenceFormProps = {
  value: TripRequestFormState;
  isSubmitting: boolean;
  onChange: (nextValue: TripRequestFormState) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
};

const travelGroupOptions: { value: TravelGroupType; label: string }[] = [
  { value: "solo", label: "Solo" },
  { value: "couple", label: "Couple" },
  { value: "family", label: "Family" },
  { value: "friends", label: "Friends" },
  { value: "group", label: "Group" },
];

const paceOptions: { value: TripPace; label: string }[] = [
  { value: "relaxed", label: "Relaxed" },
  { value: "balanced", label: "Balanced" },
  { value: "packed", label: "Packed" },
];

const accommodationOptions: { value: AccommodationType; label: string }[] = [
  { value: "hotel", label: "Hotel" },
  { value: "airbnb", label: "Airbnb" },
  { value: "hostel", label: "Hostel" },
  { value: "resort", label: "Resort" },
  { value: "no_preference", label: "No preference" },
];

const transportOptions: { value: TransportPreference; label: string }[] = [
  { value: "public_transport", label: "Public transport" },
  { value: "taxi", label: "Taxi / rideshare" },
  { value: "self_drive", label: "Self drive" },
  { value: "train", label: "Train" },
  { value: "flight", label: "Flight" },
  { value: "no_preference", label: "No preference" },
];

function updateField<T extends keyof TripRequestFormState>(
  value: TripRequestFormState,
  onChange: (nextValue: TripRequestFormState) => void,
  field: T,
  nextFieldValue: TripRequestFormState[T],
) {
  onChange({
    ...value,
    [field]: nextFieldValue,
  });
}

function FieldLabel({ children }: { children: string }) {
  return <span className="text-sm font-medium text-slate-200">{children}</span>;
}

function FieldPanel({ children }: { children: ReactNode }) {
  return <div className="space-y-2">{children}</div>;
}

function TextInput({
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  value: string;
  onChange: (nextValue: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      className="w-full rounded-2xl border border-white/10 bg-slate-950/55 px-4 py-3 text-sm text-slate-100 outline-none transition placeholder:text-slate-500 focus:border-amber-300/60 focus:ring-2 focus:ring-amber-300/20"
    />
  );
}

function SelectInput<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (nextValue: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value as T)}
      className="w-full rounded-2xl border border-white/10 bg-slate-950/55 px-4 py-3 text-sm text-slate-100 outline-none transition focus:border-amber-300/60 focus:ring-2 focus:ring-amber-300/20"
    >
      {options.map((option) => (
        <option
          key={option.value}
          value={option.value}
          className="bg-slate-950"
        >
          {option.label}
        </option>
      ))}
    </select>
  );
}

function TextAreaInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (nextValue: string) => void;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      rows={4}
      className="w-full resize-none rounded-2xl border border-white/10 bg-slate-950/55 px-4 py-3 text-sm text-slate-100 outline-none transition placeholder:text-slate-500 focus:border-amber-300/60 focus:ring-2 focus:ring-amber-300/20"
    />
  );
}

export function createTripRequestFormState(
  tripRequest: TripRequest,
): TripRequestFormState {
  return {
    destination: tripRequest.destination,
    originCity: tripRequest.originCity ?? "",
    startDate: tripRequest.startDate,
    endDate: tripRequest.endDate,
    travelersCount: String(tripRequest.travelersCount),
    travelGroupType: tripRequest.travelGroupType,
    budgetMin:
      tripRequest.budgetMin != null ? String(tripRequest.budgetMin) : "",
    budgetMax:
      tripRequest.budgetMax != null ? String(tripRequest.budgetMax) : "",
    pace: tripRequest.pace,
    accommodationType: tripRequest.accommodationType,
    transportPreference: tripRequest.transportPreference,
    interestsText: tripRequest.interests.join("\n"),
    mustVisitText: tripRequest.mustVisit.join("\n"),
    mustAvoidText: tripRequest.mustAvoid.join("\n"),
    constraintsText: tripRequest.constraints.join("\n"),
    freeTextPreferences: tripRequest.freeTextPreferences ?? "",
  };
}

export function tripRequestFormStateToTripRequest(
  value: TripRequestFormState,
): TripRequest {
  const splitLines = (text: string) =>
    text
      .split(/\r?\n|,/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0);

  const parseOptionalNumber = (text: string) => {
    if (!text.trim()) {
      return undefined;
    }

    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : undefined;
  };

  return {
    destination: value.destination.trim(),
    originCity: value.originCity.trim() || undefined,
    startDate: value.startDate,
    endDate: value.endDate,
    travelersCount: Number(value.travelersCount) || 1,
    travelGroupType: value.travelGroupType,
    budgetMin: parseOptionalNumber(value.budgetMin),
    budgetMax: parseOptionalNumber(value.budgetMax),
    pace: value.pace,
    accommodationType: value.accommodationType,
    transportPreference: value.transportPreference,
    interests: splitLines(value.interestsText),
    mustVisit: splitLines(value.mustVisitText),
    mustAvoid: splitLines(value.mustAvoidText),
    constraints: splitLines(value.constraintsText),
    freeTextPreferences: value.freeTextPreferences.trim() || undefined,
  };
}

export function TripPreferenceForm({
  value,
  isSubmitting,
  onChange,
  onSubmit,
}: TripPreferenceFormProps) {
  return (
    <form
      onSubmit={onSubmit}
      className="rounded-[2rem] border border-white/10 bg-slate-950/55 p-6 shadow-[0_30px_80px_rgba(2,6,23,0.25)] backdrop-blur"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-amber-200/80">
            Trip preferences
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-slate-50 sm:text-3xl">
            Generate a new itinerary
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            Edit the traveler profile below and submit it to the FastAPI
            generator. The dashboard will stay on the demo shell until a
            successful itinerary comes back.
          </p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-xs text-slate-300">
          <p className="font-medium text-slate-100">Backend target</p>
          <p className="mt-1">http://localhost:8000/api/trips/generate</p>
        </div>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <FieldPanel>
          <FieldLabel>Destination</FieldLabel>
          <TextInput
            value={value.destination}
            onChange={(nextValue) =>
              updateField(value, onChange, "destination", nextValue)
            }
            placeholder="Lisbon, Portugal"
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Origin city</FieldLabel>
          <TextInput
            value={value.originCity}
            onChange={(nextValue) =>
              updateField(value, onChange, "originCity", nextValue)
            }
            placeholder="New York"
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Start date</FieldLabel>
          <TextInput
            type="date"
            value={value.startDate}
            onChange={(nextValue) =>
              updateField(value, onChange, "startDate", nextValue)
            }
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>End date</FieldLabel>
          <TextInput
            type="date"
            value={value.endDate}
            onChange={(nextValue) =>
              updateField(value, onChange, "endDate", nextValue)
            }
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Travelers</FieldLabel>
          <TextInput
            type="number"
            value={value.travelersCount}
            onChange={(nextValue) =>
              updateField(value, onChange, "travelersCount", nextValue)
            }
            placeholder="2"
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Travel group</FieldLabel>
          <SelectInput
            value={value.travelGroupType}
            onChange={(nextValue) =>
              updateField(value, onChange, "travelGroupType", nextValue)
            }
            options={travelGroupOptions}
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Budget minimum</FieldLabel>
          <TextInput
            type="number"
            value={value.budgetMin}
            onChange={(nextValue) =>
              updateField(value, onChange, "budgetMin", nextValue)
            }
            placeholder="1800"
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Budget maximum</FieldLabel>
          <TextInput
            type="number"
            value={value.budgetMax}
            onChange={(nextValue) =>
              updateField(value, onChange, "budgetMax", nextValue)
            }
            placeholder="3200"
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Pace</FieldLabel>
          <SelectInput
            value={value.pace}
            onChange={(nextValue) =>
              updateField(value, onChange, "pace", nextValue)
            }
            options={paceOptions}
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Accommodation type</FieldLabel>
          <SelectInput
            value={value.accommodationType}
            onChange={(nextValue) =>
              updateField(value, onChange, "accommodationType", nextValue)
            }
            options={accommodationOptions}
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Transport preference</FieldLabel>
          <SelectInput
            value={value.transportPreference}
            onChange={(nextValue) =>
              updateField(value, onChange, "transportPreference", nextValue)
            }
            options={transportOptions}
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Free text preferences</FieldLabel>
          <TextInput
            value={value.freeTextPreferences}
            onChange={(nextValue) =>
              updateField(value, onChange, "freeTextPreferences", nextValue)
            }
            placeholder="Romantic, walkable, easy transit"
          />
        </FieldPanel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <FieldPanel>
          <FieldLabel>Interests</FieldLabel>
          <TextAreaInput
            value={value.interestsText}
            onChange={(nextValue) =>
              updateField(value, onChange, "interestsText", nextValue)
            }
            placeholder="Food\nHistory\nSunset views"
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Must visit</FieldLabel>
          <TextAreaInput
            value={value.mustVisitText}
            onChange={(nextValue) =>
              updateField(value, onChange, "mustVisitText", nextValue)
            }
            placeholder="Alfama\nBelém Tower"
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Must avoid</FieldLabel>
          <TextAreaInput
            value={value.mustAvoidText}
            onChange={(nextValue) =>
              updateField(value, onChange, "mustAvoidText", nextValue)
            }
            placeholder="Late-night clubbing\nOverpacked museum days"
          />
        </FieldPanel>

        <FieldPanel>
          <FieldLabel>Constraints</FieldLabel>
          <TextAreaInput
            value={value.constraintsText}
            onChange={(nextValue) =>
              updateField(value, onChange, "constraintsText", nextValue)
            }
            placeholder="Limit transit to under 35 minutes between key stops"
          />
        </FieldPanel>
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t border-white/10 pt-5">
        <p className="text-sm text-slate-400">
          The request is sent directly to the backend mock generation endpoint.
          No auth or database is involved yet.
        </p>
        <button
          type="submit"
          disabled={isSubmitting}
          className="inline-flex min-w-44 items-center justify-center rounded-2xl bg-amber-300 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-amber-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? "Generating itinerary..." : "Generate itinerary"}
        </button>
      </div>
    </form>
  );
}
