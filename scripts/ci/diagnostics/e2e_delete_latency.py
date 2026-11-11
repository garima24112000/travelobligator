"""Measures DELETE /trips/{id} latency on the offline E2E backend as its Local JSON store grows.

Runs inside the local CI runner container, against backend/scripts/e2e_backend.py on 127.0.0.1:8100.
Offline: the E2E backend's stand-in providers only. Prints numbers only.
"""
import glob
import http.cookiejar
import json
import os
import statistics
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8100"
TRIP = {"primary_destination": "Lisbon, Portugal", "origin_city": "Home City", "start_date": "2027-03-01",
        "end_date": "2027-03-03", "travelers_count": 2, "travel_group_type": "couple",
        "interests": ["history", "food"], "must_visit": []}


class Client:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
        started = time.monotonic()
        with self.opener.open(req, timeout=120) as resp:
            payload = json.loads(resp.read() or b"{}")
            return resp.status, payload, time.monotonic() - started


counter = [0]


def account() -> Client:
    counter[0] += 1
    c = Client()
    c.call("POST", "/auth/signup", {"email": f"lat-{os.getpid()}-{counter[0]}@example.com", "password": "e2e-password-123"})
    return c


def generated_trip(c: Client, destination: str = "Lisbon, Portugal") -> str:
    _, trip, _ = c.call("POST", "/trips", {**TRIP, "primary_destination": destination})
    trip_id = trip["data"]["trip_id"]
    _, job, _ = c.call("POST", f"/trips/{trip_id}/generate")
    job_id = job["data"]["job_id"]
    while True:
        _, state, _ = c.call("GET", f"/trips/{trip_id}/jobs/{job_id}")
        if state["data"]["status"] in ("succeeded", "failed", "interrupted"):
            return trip_id
        time.sleep(0.2)


def store_file() -> str:
    return max(glob.glob("/tmp/travelobligator_e2e_*/state.json"), key=os.path.getmtime)


def per_op_seconds(path: str) -> tuple[float, float]:
    t = time.monotonic(); data = json.loads(open(path, encoding="utf-8").read()); read = time.monotonic() - t
    t = time.monotonic()
    with open("/tmp/latency_probe.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True); f.flush(); os.fsync(f.fileno())
    write = time.monotonic() - t
    os.remove("/tmp/latency_probe.json")
    return read, write


def measure(label: str) -> None:
    times = []
    for _ in range(3):
        c = account()
        trip_id = generated_trip(c)
        status, _, seconds = c.call("DELETE", f"/trips/{trip_id}")
        assert status == 200, status
        times.append(seconds)
    path = store_file()
    read, write = per_op_seconds(path)
    print(json.dumps({"phase": label, "trips_in_store": len(json.load(open(path))["trips"]),
                      "store_mb": round(os.path.getsize(path) / 1e6, 2), "delete_s_median": round(statistics.median(times), 3),
                      "delete_s_max": round(max(times), 3), "one_full_read_s": round(read, 3), "one_full_write_fsync_s": round(write, 3)}), flush=True)


def contended(label: str) -> None:
    """A delete while another account's generation is writing to the same store (as in the browser suite)."""
    victim = account(); trip_id = generated_trip(victim)
    other = account()
    worker = threading.Thread(target=generated_trip, args=(other,))
    worker.start(); time.sleep(0.3)
    _, _, seconds = victim.call("DELETE", f"/trips/{trip_id}")
    worker.join()
    print(json.dumps({"phase": label, "delete_s_during_generation": round(seconds, 3)}), flush=True)


if __name__ == "__main__":
    measure("empty")
    contended("empty+generation")
    for target in (40, 80, 120, 160):
        while len(json.load(open(store_file()))["trips"]) < target:
            generated_trip(account())
        measure(f"{target}_trips")
        contended(f"{target}_trips+generation")
