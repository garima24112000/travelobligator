"""Section 3C.1 safe destination-geocode diagnostic (manual, ONE live request).

Asks the production geocoder for one destination exactly as generation does
and prints ONLY non-sensitive geographic fields of the top result, followed
by the plausibility verdict the app would reach:

    cd backend
    python scripts/diagnose_destination_geocode.py --query "City, Country"

Requires GEOAPIFY_API_KEY. Never prints the key, the request URL, headers,
the environment or the raw provider payload, and reports a failure by
exception TYPE only. Nothing here is imported by `backend/app`, and no
destination is known to it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
# The only provider fields this tool may print.
_PRINTABLE_FIELDS = (
    "result_type", "name", "city", "locality", "municipality", "county", "state", "country", "country_code",
    "formatted",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--query", required=True, help='the destination exactly as a trip would request it')
    args = parser.parse_args()
    if not os.environ.get("GEOAPIFY_API_KEY"):
        print("Missing required environment variable: GEOAPIFY_API_KEY. Nothing was run.")
        return 2

    sys.path.insert(0, str(_BACKEND_DIR))
    os.environ["GEOCODING_PROVIDER"] = "geoapify"

    import httpx

    from app.core.config import get_settings
    from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
    from app.providers.places import destination_resolution as resolution

    get_settings.cache_clear()
    settings = get_settings()
    try:
        with httpx.Client(timeout=settings.geoapify_timeout_seconds) as client:
            response = client.get(
                settings.geoapify_api_url.rstrip("/") + "/v1/geocode/search",
                # the same parameters the adapter sends for a destination
                params={"text": args.query, "format": "json", "limit": 1, "lang": "en",
                        "apiKey": settings.geoapify_api_key},
            )
            status = response.status_code
            results = response.json().get("results") if status == 200 else None
    except Exception as exc:  # the exception text contains the request URL: type only
        print(f"Request failed: {type(exc).__name__}")
        return 1
    if status != 200:
        print(f"Provider answered HTTP {status}.")
        return 1
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        print("Provider returned no result.")
        return 1

    result = results[0]
    print("TOP RESULT (non-sensitive geographic fields only)")
    for key in _PRINTABLE_FIELDS:
        value = result.get(key)
        print(f"- {key}: {value if isinstance(value, str) and value else '(absent)'}")

    hit = GeoapifyGeocoder()._hit(result)
    if hit is None:
        print("\nVERDICT: rejected (the result has no coordinates or identity)")
        return 1
    reason = resolution.destination_rejection_reason(args.query, resolution._hit_evidence(hit))
    print(f"\nVERDICT: {'accepted' if reason is None else 'rejected (' + reason + ')'}")
    return 0 if reason is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
