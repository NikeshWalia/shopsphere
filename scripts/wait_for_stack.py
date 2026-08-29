"""Block until the ShopSphere stack is ready to serve traffic.

    python scripts/wait_for_stack.py [--timeout 180]

Used by `make up` and by CI. It exists so that neither has to guess with a
fixed sleep: it polls the readiness probe, which only reports ready once the API
can actually reach its database. A sleep long enough to "probably" work is both
slower on a fast machine and unreliable on a loaded one.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

DEFAULT_TIMEOUT = 180.0
POLL_INTERVAL = 2.0


def probe(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed localhost URL
            body = response.read().decode("utf-8", errors="replace")
            return response.status == 200, body[:200]
    except URLError as exc:
        return False, str(exc.reason)
    except OSError as exc:
        return False, str(exc)


def wait_for(name: str, url: str, deadline: float) -> bool:
    print(f"  waiting for {name} at {url}", flush=True)
    last = ""
    while time.monotonic() < deadline:
        ok, detail = probe(url)
        if ok:
            print(f"  {name} is ready", flush=True)
            return True
        if detail != last:
            print(f"    ... {detail}", flush=True)
            last = detail
        time.sleep(POLL_INTERVAL)
    print(f"  {name} did not become ready. Last response: {last}", file=sys.stderr)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for the ShopSphere stack.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--api", default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--payment", default=os.getenv("PAYMENT_MOCK_URL", "http://localhost:9100"))
    # No environment fallback, unlike --api and --payment above. The storefront
    # is optional: the API/database/contract/security job deliberately never
    # starts it. Defaulting this to UI_BASE_URL made that job wait two minutes
    # for a service it was never going to run, and then fail - so waiting for
    # the storefront has to be an explicit request, not an ambient one.
    parser.add_argument(
        "--ui",
        default="",
        help="Also wait for the storefront at this URL. Omit to skip it entirely.",
    )
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    targets = [
        ("payment provider", f"{args.payment.rstrip('/')}/health"),
        # Readiness, not liveness: this is only 200 once the database is reachable.
        ("API", f"{args.api.rstrip('/')}/health/ready"),
    ]
    if args.ui:
        targets.append(("storefront", args.ui.rstrip("/")))

    for name, url in targets:
        if not wait_for(name, url, deadline):
            print("\nThe stack did not come up. Try:  docker compose logs", file=sys.stderr)
            return 1

    print("\nStack is ready.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
