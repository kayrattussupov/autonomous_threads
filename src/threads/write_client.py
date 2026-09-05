import time

import requests

GRAPH_BASE_URL = "https://graph.threads.net/v1.0"
MAX_RETRIES = 4
POLL_TIMEOUT_SEC = 60
POLL_INTERVAL_SEC = 5


class ThreadsAPIError(Exception):
    pass


class PublishingLimitExceeded(Exception):
    pass


class ThreadsWriteClient:
    def __init__(self, access_token: str, user_id: str):
        self._token = access_token
        self._user_id = user_id

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{GRAPH_BASE_URL}/{path}"
        params = kwargs.pop("params", {})
        params["access_token"] = self._token
        http_method = getattr(requests, method.lower())

        for attempt in range(MAX_RETRIES):
            resp = http_method(url, params=params, timeout=30, **kwargs)
            if resp.status_code == 429:
                wait = 2 ** attempt
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise ThreadsAPIError(f"{method} {path} failed: HTTP {resp.status_code} — {resp.json()}")
            return resp.json()
        raise ThreadsAPIError(f"{method} {path} failed after {MAX_RETRIES} retries: still 429")

    def create_container(self, text: str, reply_to_id: str | None = None) -> str:
        params = {"media_type": "TEXT", "text": text}
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        data = self._request("post", f"{self._user_id}/threads", params=params)
        return data["id"]

    def get_container_status(self, container_id: str) -> str:
        data = self._request("get", container_id, params={"fields": "status"})
        return data["status"]

    def wait_until_ready(self, container_id: str, timeout_sec: int = POLL_TIMEOUT_SEC, poll_interval_sec: int = POLL_INTERVAL_SEC) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            status = self.get_container_status(container_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsAPIError(f"container {container_id} ended in status {status}")
            time.sleep(poll_interval_sec)
        raise ThreadsAPIError(f"container {container_id} did not finish within {timeout_sec}s")

    def publish_container(self, container_id: str) -> str:
        data = self._request("post", f"{self._user_id}/threads_publish", params={"creation_id": container_id})
        return data["id"]

    def publish_text_post(self, text: str, reply_to_id: str | None = None) -> str:
        self.check_publishing_limit(kind="posts")
        container_id = self.create_container(text, reply_to_id=reply_to_id)
        self.wait_until_ready(container_id)
        return self.publish_container(container_id)

    def reply_to_post(self, post_id: str, text: str) -> str:
        self.check_publishing_limit(kind="replies")
        container_id = self.create_container(text, reply_to_id=post_id)
        self.wait_until_ready(container_id)
        return self.publish_container(container_id)

    def get_media_insights(self, media_id: str) -> dict:
        data = self._request(
            "get", media_id + "/insights",
            params={"metric": "views,likes,replies,reposts,quotes,shares"},
        )
        result = {"views": 0, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0, "shares": 0}
        for metric in data.get("data", []):
            values = metric.get("values", [{}])
            result[metric["name"]] = values[0].get("value", 0) if values else 0
        return result

    def get_replies(self, media_id: str) -> list[dict]:
        """GET /{media_id}/replies — replies under the caller's own post.
        Official API (SPEC.md §4: publish/replies/insights go through the
        API, not the browser). The exact response shape isn't verified
        against live data anywhere in this codebase (same caveat as
        check_publishing_limit(kind="replies")) — confirm field names
        during a live smoke test and adjust here if they differ."""
        data = self._request(
            "get", f"{media_id}/replies",
            params={"fields": "id,text,username,timestamp,permalink"},
        )
        return data.get("data", [])

    def check_publishing_limit(self, kind: str = "posts") -> dict:
        data = self._request("get", f"{self._user_id}/threads_publishing_limit")
        entry = data["data"][0]
        if kind == "replies":
            # Best-effort field names for the reply quota — NOT verified against a live
            # Threads API response (no credentials available at implementation time).
            # Confirm against real data during Task 7's Step 5 live smoke test, and fix
            # the field names here if the actual API response shape differs.
            usage = entry.get("reply_quota_usage", entry.get("quota_usage"))
            total = entry.get("reply_config", entry.get("config"))["quota_total"]
        else:
            usage, total = entry["quota_usage"], entry["config"]["quota_total"]
        if usage >= total:
            raise PublishingLimitExceeded(f"{kind} limit exhausted: {usage}/{total} in the current 24h window")
        return {"usage": usage, "total": total}

    def refresh_access_token(self) -> str:
        data = self._request(
            "get", "refresh_access_token",
            params={"grant_type": "th_refresh_token"},
        )
        self._token = data["access_token"]
        return self._token
