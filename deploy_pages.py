"""Deploy the exact uploaded artifact ID through the documented Pages REST API."""
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(url, token, payload=None):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, headers=headers), timeout=60) as response:
        return json.load(response)


def main():
    # Tokens stay in memory and are never printed or written to an artifact.
    identity = request(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"], os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])["value"]
    token = os.environ["GH_TOKEN"]
    root = "https://api.github.com/repos/" + os.environ["GITHUB_REPOSITORY"] + "/pages/deployments"
    deployment = request(root, token, {"artifact_id": int(os.environ["PAGES_ARTIFACT_ID"]), "pages_build_version": os.environ["GITHUB_SHA"], "oidc_token": identity})
    print("Pages deployment created:", deployment["id"], flush=True)
    for _ in range(60):
        state = request(root + "/" + str(deployment["id"]), token)
        status = state["status"]
        print("Pages deployment status:", status, flush=True)
        if status in {"succeed", "succeeded", "success"}:
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write("page_url=" + deployment["page_url"] + "\n")
            return
        if status not in {"queued", "in_progress", "deployment_in_progress", "pending", "waiting"}:
            raise RuntimeError("Pages deployment failed: " + status)
        time.sleep(5)
    raise RuntimeError("Pages deployment timed out")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as exc:
        # HTTP response diagnostics contain no request authorization headers/tokens.
        print("Pages API error:", exc.code, exc.read().decode(), flush=True)
        raise SystemExit(1)
