import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    max_upload_mb: int = 20
    api_key: str | None = None
    log_level: str = "INFO"
    validation_timeout_s: float = 30
    parse_timeout_s: float = 30
    parse_workers: int = 2
    parse_memory_mb: int = 1024
    read_timeout_s: float = 60
    max_concurrent_requests: int = 16
    expose_docs: bool | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", cls.max_upload_mb)),
            api_key=os.environ.get("API_KEY") or None,
            log_level=os.environ.get("LOG_LEVEL", cls.log_level),
            validation_timeout_s=float(
                os.environ.get("VALIDATION_TIMEOUT_S", cls.validation_timeout_s)
            ),
            parse_timeout_s=float(os.environ.get("PARSE_TIMEOUT_S", cls.parse_timeout_s)),
            parse_workers=int(os.environ.get("PARSE_WORKERS", cls.parse_workers)),
            parse_memory_mb=int(os.environ.get("PARSE_MEMORY_MB", cls.parse_memory_mb)),
            read_timeout_s=float(os.environ.get("READ_TIMEOUT_S", cls.read_timeout_s)),
            max_concurrent_requests=int(
                os.environ.get("MAX_CONCURRENT_REQUESTS", cls.max_concurrent_requests)
            ),
            expose_docs=_flag(os.environ.get("EXPOSE_DOCS")),
        )

    @property
    def docs_enabled(self) -> bool:
        return self.api_key is None if self.expose_docs is None else self.expose_docs


def _flag(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in ("1", "true", "yes", "on")
