"""Bounded, read-only health telemetry for a catalogued official root."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.downloader import (
    DownloadError,
    DownloadHttpError,
    DownloadMediaTypeError,
    DownloadNetworkError,
    DownloadResult,
    DownloadSecurityError,
    DownloadStorageError,
    DownloadTimeout,
    DownloadTooLarge,
    download_public_file,
)
from scripts.source_policy import canonical_site_identity


MAX_RESPONSE_BYTES = 1_048_576
TOTAL_TIMEOUT_SECONDS = 5.0
_STATES = frozenset({"healthy", "redirect_review", "unavailable"})
_REASONS = frozenset({"timeout", "dns_or_network", "http_error", "unsupported_content_type", "response_too_large", "security_rejection", "storage_error"})
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


def _domain(url: str) -> str:
    """Return a display-safe host[:nondefault-port], or an empty string."""
    if not isinstance(url, str) or not url or url != url.strip() or any(c.isspace() for c in url):
        return ""
    try:
        part = urlsplit(url)
        port = part.port
    except ValueError:
        return ""
    if part.scheme.casefold() not in {"http", "https"} or not part.hostname or part.username is not None or part.password is not None:
        return ""
    host = part.hostname.rstrip(".").casefold()
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    default = (part.scheme.casefold() == "http" and port == 80) or (part.scheme.casefold() == "https" and port == 443)
    return host if port is None or default else f"{host}:{port}"


def _utc_timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime):
        raise ValueError("invalid clock")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class LiveSmokeResult:
    province: str
    status: str
    requested_domain: str
    final_domain: str | None
    redirect_domains: tuple[str, ...]
    checked_at: str
    content_type: str | None
    size_bytes: int | None
    reason_code: str | None
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0" or self.status not in _STATES or not self.province or not self.requested_domain:
            raise ValueError("invalid live smoke result")
        if not self.redirect_domains or any(not item or "://" in item or "/" in item for item in self.redirect_domains):
            raise ValueError("invalid redirect domains")
        if self.status == "unavailable":
            if self.final_domain is not None or self.content_type is not None or self.size_bytes is not None or self.reason_code not in _REASONS:
                raise ValueError("invalid unavailable result")
        elif self.final_domain is None or self.content_type is None or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("invalid successful result")
        elif (self.status == "healthy") != (self.reason_code is None):
            raise ValueError("invalid successful result reason")
        elif self.status == "redirect_review" and self.reason_code != "unlisted_redirect_domain":
            raise ValueError("invalid redirect review")

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "content_type": self.content_type,
            "final_domain": self.final_domain,
            "province": self.province,
            "reason_code": self.reason_code,
            "redirect_domains": list(self.redirect_domains),
            "requested_domain": self.requested_domain,
            "schema_version": self.schema_version,
            "size_bytes": self.size_bytes,
            "status": self.status,
        }


def _reason(error: DownloadError) -> str:
    if isinstance(error, DownloadTimeout): return "timeout"
    if isinstance(error, DownloadNetworkError): return "dns_or_network"
    if isinstance(error, DownloadHttpError): return "http_error"
    if isinstance(error, DownloadMediaTypeError): return "unsupported_content_type"
    if isinstance(error, DownloadTooLarge): return "response_too_large"
    if isinstance(error, DownloadSecurityError): return "security_rejection"
    if isinstance(error, DownloadStorageError): return "storage_error"
    return "storage_error"


def check_official_root(province: str, official_roots: tuple[str, ...], *, downloader: Callable[..., DownloadResult] = download_public_file, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> LiveSmokeResult:
    """Check the first authenticated root using the secure downloader seam."""
    if not isinstance(province, str) or not official_roots or not all(_domain(root) for root in official_roots):
        raise ValueError("invalid health check input")
    requested_url, requested_domain = official_roots[0], _domain(official_roots[0])
    known_domains = (requested_domain,)
    timestamp = _utc_timestamp(clock)
    with tempfile.TemporaryDirectory(prefix="live-smoke-") as temporary:
        workspace = Path(temporary).resolve()
        try:
            result = downloader(requested_url, workspace, max_bytes=MAX_RESPONSE_BYTES, timeout=TOTAL_TIMEOUT_SECONDS)
            if not isinstance(result, DownloadResult) or not isinstance(result.path, Path):
                raise ValueError("forged download result")
            chain = tuple(result.redirect_chain)
            domains = tuple(_domain(item) for item in chain)
            if not chain or not all(domains) or canonical_site_identity(chain[0]) != canonical_site_identity(requested_url) or chain[-1] != result.source_url or _domain(result.source_url) != domains[-1] or result.path.parent.resolve() != workspace or not result.path.is_file() or result.size_bytes < 0 or result.path.stat().st_size != result.size_bytes or not isinstance(result.media_type, str) or not _MEDIA_TYPE.fullmatch(result.media_type):
                raise ValueError("forged download result")
            ordered = tuple(dict.fromkeys(domains))
            allowlisted = {canonical_site_identity(root) for root in official_roots}
            status = "healthy" if all(canonical_site_identity(item) in allowlisted for item in chain) else "redirect_review"
            return LiveSmokeResult(province, status, requested_domain, domains[-1], ordered, timestamp, result.media_type, result.size_bytes, None if status == "healthy" else "unlisted_redirect_domain")
        except DownloadError as error:
            return LiveSmokeResult(province, "unavailable", requested_domain, None, known_domains, timestamp, None, None, _reason(error))
        except (OSError, TypeError, ValueError):
            return LiveSmokeResult(province, "unavailable", requested_domain, None, known_domains, timestamp, None, None, "storage_error")
        finally:
            try:
                for child in workspace.iterdir():
                    if child.is_file() or child.is_symlink(): child.unlink(missing_ok=True)
                    elif child.is_dir(): shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None: raise ValueError("non-finite")


def _load_province(token: str) -> tuple[str, tuple[str, ...]]:
    path = Path(__file__).resolve().parents[1] / "references" / "provinces" / "index.json"
    payload = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_constant)
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "verified_at", "coverage_note", "mode_authority_urls", "provinces"} or payload["schema_version"] != "1.0" or not isinstance(payload["provinces"], list): raise ValueError("invalid catalog")
    needle = unicodedata.normalize("NFKC", token).casefold()
    matches = []
    for record in payload["provinces"]:
        if not isinstance(record, dict) or set(record) != {"province", "aliases", "mode", "authority_name", "official_roots", "mode_source_url", "verified_at", "notes"}: raise ValueError("invalid catalog")
        roots = record["official_roots"]
        if not isinstance(record["province"], str) or not isinstance(record["aliases"], list) or not isinstance(roots, list) or not roots or not all(isinstance(root, str) and _domain(root) and urlsplit(root).scheme == "https" for root in roots): raise ValueError("invalid catalog")
        if needle in {unicodedata.normalize("NFKC", item).casefold() for item in [record["province"], *record["aliases"]] if isinstance(item, str)}: matches.append((record["province"], tuple(roots)))
    if len(matches) != 1: raise ValueError("unknown province")
    return matches[0]


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("invalid arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--province", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        province, roots = _load_province(arguments.province)
        output = check_official_root(province, roots)
        sys.stdout.write(json.dumps(output.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    except SystemExit as error:
        if error.code == 0:
            return 0
        sys.stderr.write("live-smoke: invalid input\n")
        return 2
    except (OSError, UnicodeError, TypeError, ValueError):
        sys.stderr.write("live-smoke: invalid input\n")
        return 2


__all__ = ["LiveSmokeResult", "MAX_RESPONSE_BYTES", "TOTAL_TIMEOUT_SECONDS", "check_official_root", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
