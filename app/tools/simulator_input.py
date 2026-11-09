#!/usr/bin/env python3
"""Send local test input to a Simulator host launched with N3DS_AUTOMATION=1."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="Dedicated Simulator UDID")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--buttons", type=lambda s: int(s, 0), default=0, help="n3ds_button bit mask")
    parser.add_argument("--hold", type=float, default=.25)
    args = parser.parse_args()
    if not 0 <= args.hold <= 5:
        parser.error("--hold must be between 0 and 5 seconds")
    result = subprocess.run(["xcrun", "simctl", "get_app_container", args.device, args.bundle, "data"],
                            text=True, capture_output=True, check=True)
    documents = Path(result.stdout.strip()) / "Documents"
    command = documents / "simulator-input.json"
    state = documents / "simulator-state.json"

    def send(buttons: int, *, acknowledge: bool = True) -> dict:
        sequence = str(uuid.uuid4())
        temporary = command.with_suffix(".tmp")
        temporary.write_text(json.dumps({"sequence": sequence, "buttons": buttons}))
        temporary.replace(command)
        if not acknowledge:
            return {}
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = json.loads(state.read_text()) if state.exists() else {}
            if current.get("sequence") == sequence:
                return current
            time.sleep(.05)
        raise RuntimeError("Simulator did not acknowledge input; launch with SIMCTL_CHILD_N3DS_AUTOMATION=1")

    try:
        current = send(args.buttons)
        time.sleep(args.hold)
    finally:
        # Release even after an interrupted hold or failed acknowledgement.
        send(0, acknowledge=False)
    print(json.dumps(current, indent=2))


if __name__ == "__main__":
    main()
