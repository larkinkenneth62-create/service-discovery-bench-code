from __future__ import annotations

import json
import sys

from provider_boundary import mock_generate, validate_provider_request


def main() -> None:
    request = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    keys = validate_provider_request(request)
    response = mock_generate(**request)
    sys.stdout.buffer.write((json.dumps({"received_keys": keys, "response": response}, sort_keys=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
