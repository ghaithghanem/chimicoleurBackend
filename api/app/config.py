from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    # Read-only key used by the Flutter app.
    app_api_key: str = Field(min_length=16)
    # Write key used only by the collector for POST /ingest.
    ingest_api_key: str = Field(min_length=16)

    heartbeat_interval_s: int = 60
    default_source: str = "plc_line_2"
    # "source=Label,source2=Label 2"
    source_labels: str = "plc_line_2=Line 2"

    display_tz: str = "Africa/Tunis"
    # Equal-length shifts covering 24 h, first one starting at shift_start (local time).
    shift_names: str = "A,B,C"
    shift_start: str = "06:00"

    max_buckets: int = 2000
    ingest_max_batch: int = 5000

    @property
    def stale_after_s(self) -> int:
        return 2 * self.heartbeat_interval_s

    @property
    def labels(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in self.source_labels.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def label_for(self, source: str) -> str:
        return self.labels.get(source, source)

    @property
    def tz_offset_s(self) -> int:
        # Bucketing uses a fixed UTC offset. Africa/Tunis has had no DST since 2009 (UTC+1).
        off = datetime.now(timezone.utc).astimezone(ZoneInfo(self.display_tz)).utcoffset()
        return int(off.total_seconds()) if off else 0

    @property
    def shifts(self) -> list[str]:
        return [s.strip() for s in self.shift_names.split(",") if s.strip()]

    @property
    def shift_anchor_s(self) -> int:
        h, m = self.shift_start.split(":")
        return int(h) * 3600 + int(m) * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
