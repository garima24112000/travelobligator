from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.providers.itinerary_narrator.contract import NARRATOR_SYSTEM_PROMPT, build_grounded_prompt_body
from app.core import performance
from app.core.config import get_settings
from app.models.itinerary_narrative import (
    GettingAroundProfile,
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
    validate_narrative_against_request,
)
from app.providers.ai_failure import AIProviderFailureKind, classify_ai_provider_exception
from app.providers.ai_stage_budget import StageRun
from app.providers.itinerary_narrator.base import ItineraryNarratorProvider
from app.providers.itinerary_narrator.structural_retry import (
    MAX_NARRATOR_ATTEMPTS,
    RETRY_FORMAT_REMINDER,
    log_retry_outcome,
    log_retrying,
    structural_failure_message,
)

# Groq-backed itinerary narrator adapter (Step 182F,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# Mirrors `app.providers.ai_candidate_proposal.groq_adapter`'s structure
# (same `langchain_groq.ChatGroq` structured-output pattern, same lazy
# import, same no-key-never-crashes contract) but is a completely
# separate feature: this adapter only ever turns an
# `ItineraryNarrativeRequest` (a strict allow-list of already-computed
# `PlanningState` fields) into prose, never a candidate proposal, and it
# is gated by `Settings.itinerary_narrator_enabled`/
# `itinerary_narrator_provider`, never
# `ai_candidate_discovery_shadow_mode_enabled`/
# `ai_candidate_proposal_provider`.
#
# The structured-output schema below has no field for a price, rating,
# review count, route/travel-time duration, booking link, availability
# flag, opening hour, or clock time -- narrative text and caveats are the
# only content fields. Every day's real `date` is taken from `request`
# itself (looked up by the LLM-returned `day_number`), never from
# anything the model could hallucinate, and a `day_number` the request
# doesn't actually contain is dropped rather than invented into a new day.
#
# The `langchain_groq` package import is deliberately deferred into
# `_build_client` (not a module-level import), matching the AI
# candidate-proposal Groq adapter exactly, so the rest of the app -- and
# every test that injects a fake client or exercises the no-key path --
# keeps working whether or not the package is installed.


class _NarratorDayOutputSchema(BaseModel):
    """Section 195 lesson (same as the Section 191A.1/193B ones): Groq's
    `json_schema` strict mode requires every property to appear in the
    schema's `required` array -- a field with `default_factory=...`
    (making it Python-optional) is silently excluded from `required` by
    `langchain_groq`'s schema conversion, and Groq's own strict-mode
    validator then rejects the whole request outright (`400 invalid JSON
    schema for response_format`) before any completion is even
    attempted. Every field below therefore has no Python default at all
    -- always required, with the prompt instructed (matching
    `_GroqItineraryReasoningSchema`'s own established convention) to use
    an empty list/null when there is nothing to report, never to omit
    the key.
    """

    day_number: int = Field(description="Must match one of the day_number values given in the input.")
    title: str = Field(description="A short, traveler-facing title for this day.")
    narrative: str = Field(
        description="Polished prose summarizing this day's scheduled places. Never invent a "
        "hotel, flight, price, rating, review count, route/travel-time duration, booking "
        "confirmation, or clock time not already present in the input."
    )
    caveats: list[str] = Field(
        description="Short caveats to preserve, e.g. when movement/route data wasn't "
        "available. Always include this key; use an empty list if there are none."
    )
    referenced_experience_ids: list[str] = Field(
        description="The experience_id value(s) (given alongside each scheduled place below) "
        "this day's narrative is actually about. Never an id from a different day, and never "
        "an invented id. Always include this key; use an empty list if unsure."
    )


class _NarratorBatchSchema(BaseModel):
    summary: str = Field(description="A short, trip-level polished summary.")
    daily_narratives: list[_NarratorDayOutputSchema] = Field(
        description="One entry per day. Always include this key; use an empty list if none apply."
    )
    assumptions: list[str] = Field(
        description="Always include this key. Use an empty list if there are none."
    )
    warnings: list[str] = Field(
        description="Always include this key. Use an empty list if there are none."
    )
    # Declared before the advisory so the model commits to a profile first.
    getting_around_profile: Literal[
        "transit_walk", "rail_walk", "taxi_driver_walk", "car_rideshare", "mixed", ""
    ] = Field(
        description="The broad visitor transport pattern that fits this destination (see the "
        "profile definitions). Always include this key; an empty string only if the destination "
        "is ambiguous or unrecognized."
    )
    getting_around_advisory: str = Field(
        description="Expected for any recognized destination: one concise sentence of general "
        "guidance on the transport pattern generally practical for a visitor there (generic "
        "modes only). Always include this key; an empty string only if the destination is "
        "ambiguous or unrecognized."
    )


_VALIDATION_FAILED_MESSAGE = (
    "AI narration output did not meet the required structure or grounding rules and was not used."
)

_SYSTEM_PROMPT = NARRATOR_SYSTEM_PROMPT


def _build_prompt(request: ItineraryNarrativeRequest) -> str:
    """Section 202B.3: the shared, grounded prompt body (see `contract.py`)."""
    return "\n".join([_SYSTEM_PROMPT, "", build_grounded_prompt_body(request)])



class GroqItineraryNarratorProvider(ItineraryNarratorProvider):
    """`ItineraryNarratorProvider` implementation backed by Groq (via
    `langchain_groq.ChatGroq`), used only when explicitly config-gated in
    via `get_itinerary_narrator_provider` -- never the default.

    `narrate` never invents a hotel, flight, price, rating, review count,
    route/travel-time duration, booking confirmation, or clock time: the
    structured-output schema Groq must respond through has no such
    field, every day's real date is taken from `request` (never the
    model's own output), and a `day_number` the request doesn't contain
    is dropped. If validation fails, the call raises, or no usable
    structured output comes back, this returns an honest `failed` result
    with no narrative -- never a fabricated one.
    """

    provider_name = "groq_itinerary_narrator_provider"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 2500,
        temperature: float = 0.4,
    ) -> None:
        settings = get_settings()

        self._client = client
        if api_key is None and client is None:
            self._api_key = settings.groq_api_key
        else:
            self._api_key = api_key
        self._model = (
            model
            if model is not None
            else (settings.itinerary_narrator_model or settings.groq_model)
        )
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_seconds = settings.itinerary_narrator_timeout_seconds

    def narrate(self, request: ItineraryNarrativeRequest) -> ItineraryNarrativeReport:
        if self._client is None and not self._api_key:
            return self._not_connected_result(
                "Groq API key is not configured (GROQ_API_KEY unset)."
            )

        # Section 1C: every attempt of this stage answers to ONE total
        # wall-clock budget, and the stage makes at most one recovery attempt
        # (structural OR transport, never both stacked). Whatever does not
        # succeed is narrated by the deterministic fallback, as before.
        settings = get_settings()
        run = StageRun(
            "groq_narrator",
            total_budget_seconds=settings.groq_narrator_total_budget_seconds,
            request_timeout_seconds=self._timeout_seconds,
            structural_retries=MAX_NARRATOR_ATTEMPTS - 1,
            transport_retries=settings.groq_max_retries,
        )
        report = self._narrate_within(request, run)
        run.close(completed=report.status == ItineraryNarrativeStatus.SUCCESS)
        return report

    def _narrate_within(self, request: ItineraryNarrativeRequest, run: StageRun) -> ItineraryNarrativeReport:
        def deadline_result() -> ItineraryNarrativeReport:
            return self._failed_result(
                run.deadline_message("Groq"), failure_kind=AIProviderFailureKind.DEADLINE_EXCEEDED.value
            )

        # Section 202C.1D: one initial attempt + at most one retry for a
        # STRUCTURAL output failure (see `structural_retry.py`), with the same
        # factual input plus a format reminder. Section 1C: that retry (like
        # the single transport retry) only starts while a meaningful part of
        # the stage budget is left -- a malformed first answer late in the
        # budget goes straight to the deterministic fallback. Never retried:
        # auth, a non-transient provider error, and any parsed result that
        # `_build_result` rejects.
        prompt = _build_prompt(request)
        structural_retry = False
        while True:
            timeout = run.next_attempt_timeout()
            if timeout is None:
                return deadline_result()
            if self._client is not None:
                client = self._client
            else:
                try:
                    client = self._build_client(timeout=timeout)
                except Exception as exc:  # missing package / bad config -> not_connected
                    run.attempts = 0  # no request was made
                    return self._not_connected_result(f"Groq client could not be initialized: {exc}")

            attempt_prompt = f"{prompt}\n\n{RETRY_FORMAT_REMINDER}" if structural_retry else prompt
            try:
                with performance.provider_call("groq_narrator"):
                    raw_output = client.invoke(attempt_prompt)
            except Exception as exc:  # API/runtime/timeout failure -> failed, never fabricated
                structural, failure_message = structural_failure_message("Groq", exc)
                if not structural:
                    if run.allow_transport_retry(exc):
                        continue
                    if run.deadline_exceeded:
                        return deadline_result()
                    return self._failed_result(
                        failure_message, failure_kind=classify_ai_provider_exception(exc).value
                    )
                run.answered = True
            else:
                run.answered = True
                output_dict = self._coerce_output(raw_output)
                if output_dict is not None:
                    if structural_retry:
                        log_retry_outcome(self.provider_name, recovered=True)
                    return self._build_result(request, output_dict)
                failure_message = "Groq did not return a structured response."

            if not run.allow_structural_retry():
                if structural_retry:
                    log_retry_outcome(self.provider_name, recovered=False)
                if run.deadline_exceeded:
                    return deadline_result()
                return self._failed_result(
                    failure_message, failure_kind=AIProviderFailureKind.MALFORMED_OUTPUT.value
                )
            structural_retry = True
            log_retrying(self.provider_name)

    def _build_client(self, timeout: float | None = None) -> Any:
        """Lazily imports and constructs the real Groq client, bound to
        the structured-output schema. Kept inside a method (never a
        module-level import) so the rest of the app imports cleanly
        whether or not the `langchain_groq` package is installed, and so
        tests never need it installed either.
        """
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise RuntimeError("The 'langchain_groq' package is not installed.") from exc

        chat = ChatGroq(
            model=self._model,
            api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            # Section 1C: the timeout of THIS attempt (never more than the
            # stage budget has left), and no hidden SDK retries -- the one
            # recovery attempt is made by `narrate`, under the stage budget.
            timeout=timeout if timeout is not None else self._timeout_seconds,
            max_retries=0,
        )
        # Section 195 (Task 18): switched to Structured Outputs
        # (`method="json_schema", strict=True`) -- the Section 191A.1
        # fix this adapter had not yet adopted -- now that its output
        # contract is changing anyway (`referenced_experience_ids`). No
        # `tools`/`tool_choice` sent alongside it.
        return chat.with_structured_output(_NarratorBatchSchema, method="json_schema", strict=True)

    @staticmethod
    def _coerce_output(raw_output: Any) -> dict[str, Any] | None:
        if isinstance(raw_output, dict):
            return raw_output
        model_dump = getattr(raw_output, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except Exception:
                return None
            return dumped if isinstance(dumped, dict) else None
        return None

    def _build_result(
        self, request: ItineraryNarrativeRequest, output: dict[str, Any]
    ) -> ItineraryNarrativeReport:
        summary = output.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return self._failed_result("Groq output did not include a usable summary.")

        real_dates_by_day_number = {day.day_number: day.date for day in request.days}

        raw_daily = output.get("daily_narratives")
        if not isinstance(raw_daily, list):
            return self._failed_result("Groq output did not include a daily_narratives list.")

        daily_narratives: list[ItineraryNarrativeDayOutput] = []
        for raw_day in raw_daily:
            if not isinstance(raw_day, dict):
                return self._failed_result("Groq output contained a malformed day entry.")
            day_number = raw_day.get("day_number")
            real_date = real_dates_by_day_number.get(day_number)
            if real_date is None:
                # The model referenced a day that isn't in our own
                # request -- dropped rather than invented into existence.
                continue
            try:
                daily_narratives.append(
                    ItineraryNarrativeDayOutput(
                        day_number=day_number,
                        date=real_date,
                        title=raw_day.get("title", ""),
                        narrative=raw_day.get("narrative", ""),
                        caveats=[c for c in raw_day.get("caveats", []) if isinstance(c, str)],
                        referenced_experience_ids=[
                            i for i in raw_day.get("referenced_experience_ids", []) if isinstance(i, str)
                        ],
                    )
                )
            except ValidationError as exc:
                return self._failed_result(_VALIDATION_FAILED_MESSAGE)

        assumptions = [a for a in output.get("assumptions", []) if isinstance(a, str)]
        warnings = [w for w in output.get("warnings", []) if isinstance(w, str)]
        if request.truncated:
            assumptions = assumptions + [
                "Input was truncated for length; some days/items were omitted from the narrator's view."
            ]

        try:
            report = ItineraryNarrativeReport(
                status=ItineraryNarrativeStatus.SUCCESS,
                provider=self.provider_name,
                model=self._model,
                summary=summary.strip(),
                daily_narratives=daily_narratives,
                warnings=warnings,
                assumptions=assumptions,
                source_fields_used=_source_fields_used(request),
                generated_at=datetime.now(timezone.utc),
                # Raw here; sanitized by the service, never by this adapter.
                getting_around_advisory=_raw_advisory(output),
                getting_around_profile=GettingAroundProfile.parse(output.get("getting_around_profile")),
            )
        except ValidationError as exc:
            return self._failed_result(_VALIDATION_FAILED_MESSAGE)

        # Section 195 (Task 12): structural place-identity safety check --
        # even schema-valid, domain-valid output must still be checked
        # against request.days' own real experience_id sets before this
        # adapter can ever report success.
        violations = validate_narrative_against_request(request, report)
        if violations:
            return self._failed_result(
                "Groq output referenced an experience_id outside that day's real scheduled "
                "items: " + "; ".join(violations)
            )

        return report

    def _not_connected_result(self, reason: str) -> ItineraryNarrativeReport:
        return ItineraryNarrativeReport(
            status=ItineraryNarrativeStatus.NOT_CONNECTED,
            provider=self.provider_name,
            model=self._model,
            message=reason,
        )

    def _failed_result(self, reason: str, failure_kind: str | None = None) -> ItineraryNarrativeReport:
        return ItineraryNarrativeReport(
            status=ItineraryNarrativeStatus.FAILED,
            provider=self.provider_name,
            model=self._model,
            message=reason,
            failure_kind=failure_kind,
        )


def _raw_advisory(output: dict[str, Any]) -> str | None:
    advisory = output.get("getting_around_advisory")
    return (advisory.strip() or None) if isinstance(advisory, str) else None


def _source_fields_used(request: ItineraryNarrativeRequest) -> list[str]:
    """Names which allow-listed `ItineraryNarrativeRequest` fields were
    actually populated for this call -- Developer-view transparency
    bookkeeping only, never a claim about output accuracy.
    """
    used = ["destination", "start_date", "end_date", "travelers_count", "days"]
    if request.travel_group_type:
        used.append("travel_group_type")
    if request.pace:
        used.append("pace")
    if request.interests:
        used.append("interests")
    if request.weather_available:
        used.append("weather_summary")
    if request.stay_area_names:
        used.append("stay_area_names")
    if request.accommodation_offer_count:
        used.append("accommodation_offer_count")
    if request.flight_offer_count:
        used.append("flight_offer_count")
    if request.validation_status:
        used.append("validation_status")
    if request.unavailable_data_fields:
        used.append("unavailable_data_fields")
    # Section 202B.3: reasoning rationale/strategy prose is no longer sent to
    # the narrator, so it is never reported as a used source field.
    if request.was_adjusted_after_feasibility_checks:
        used.append("was_adjusted_after_feasibility_checks")
    return used
