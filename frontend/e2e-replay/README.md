# Offline browser replay of one saved trip (M2 Phase 2)

`m2p2-snapshot.spec.ts` opens ONE trip saved by the bounded live verification in the real production
frontend and checks both views. Every `/api` request the page makes is answered from the snapshot the
verification wrote; map tiles are answered locally; any other outside request is aborted, and so is
any write. No backend, provider, key or database is involved.

    # 1. the bounded live generation (development only) -- writes the snapshot
    cd backend
    python scripts/verify_m2_phase2.py --e2e-only --airport-data /tmp/ourairports-airports.csv \
        --ui-snapshot /tmp/m2p2_ui.json --execute --out /tmp/m2p2_e2e.json

    # 2. the replay (offline)
    cd ../frontend
    M2P2_UI_SNAPSHOT=/tmp/m2p2_ui.json npm run e2e:replay

Every expectation is computed from the snapshot (airport note, price, booking link, reasons, route
summary). The test fails if the page requests an API path the snapshot does not hold, or tries to write.
A synthetic snapshot for developing the spec comes from the hermetic test:
`M2P2_WRITE_SNAPSHOT=<path> pytest backend/app/tests/scripts/test_verify_m2_phase2.py -k v4_generation`.

What this does not show: real map tiles, screen readers, other browsers, or a live backend's own
behaviour after the snapshot was taken. It is not part of CI (own config, own directory).
