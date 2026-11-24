import httpx

from .buffer import Row


class RejectedBatch(Exception):
    """The API refused the data itself (HTTP 422): retrying the same rows will never succeed."""


class ApiSink:
    def __init__(self, base_url: str, api_key: str, verify_tls: bool = True, timeout_s: float = 10.0):
        self._client = httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, verify=verify_tls,
                                    timeout=timeout_s)

    def send(self, rows: list[Row]) -> None:
        r = self._client.post("/ingest", json={"readings": [row.to_json() for row in rows]})
        if r.status_code == 422:
            raise RejectedBatch(r.text[:500])
        r.raise_for_status()

    def close(self) -> None:
        self._client.close()
