"""Bounded, read-only health telemetry for a catalogued official root."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import stat
import sys
import tempfile
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
    DownloadRedirectError,
    DownloadResult,
    DownloadSecurityError,
    DownloadStorageError,
    DownloadTimeout,
    DownloadTooLarge,
    download_public_file,
)
from scripts.downloader import _MEDIA_TYPE_EXTENSIONS
from scripts.query_plan import load_province_catalog
from scripts.source_policy import canonical_site_identity


MAX_RESPONSE_BYTES = 1_048_576
TOTAL_TIMEOUT_SECONDS = 5.0
_STATES = frozenset({"healthy", "redirect_review", "unavailable"})
_REASONS = frozenset({"timeout", "dns_or_network", "http_error", "redirect_error", "unsupported_content_type", "response_too_large", "security_rejection", "storage_error"})
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_CATALOG_PATH = Path(__file__).parent.parent / "references" / "provinces" / "index.json"
# IANA reserves ``alt`` for special use and ``arpa`` exclusively for Internet
# infrastructure.  Blocking the complete namespaces also covers future ARPA
# delegations without treating ordinary digit-bearing public DNS names as local.
_NON_WEB_DNS_SUFFIXES = (
    "alt",
    "arpa",
    "example",
    "internal",
    "invalid",
    "local",
    "localhost",
    "onion",
    "test",
)


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
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in _NON_WEB_DNS_SUFFIXES):
        return ""
    labels = host.split(".")
    if len(labels) < 2 or len(host) > 253 or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") or not all(char.isascii() and (char.isalnum() or char == "-") for char in label) for label in labels):
        return ""
    if all(label.isdecimal() for label in labels) or any(label.casefold().startswith("0x") for label in labels):
        return ""
    default = (part.scheme.casefold() == "http" and port == 80) or (part.scheme.casefold() == "https" and port == 443)
    return host if port is None or default else f"{host}:{port}"


def _safe_url(url: object, *, catalog: bool = False, allow_http: bool = False) -> bool:
    if not isinstance(url, str) or not _domain(url):
        return False
    try:
        part = urlsplit(url)
    except ValueError:
        return False
    if part.scheme.casefold() not in ({"http", "https"} if allow_http else {"https"}) or part.username is not None or part.password is not None:
        return False
    if catalog and (part.query or part.fragment):
        return False
    return True


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
        if self.schema_version != "1.0" or self.status not in _STATES or not isinstance(self.province, str) or not self.province or any(char in self.province for char in "/\\?:#@") or not isinstance(self.requested_domain, str) or not self.requested_domain:
            raise ValueError("invalid live smoke result")
        if not isinstance(self.checked_at, str) or not _UTC.fullmatch(self.checked_at):
            raise ValueError("invalid checked time")
        try:
            if datetime.strptime(self.checked_at, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") != self.checked_at:
                raise ValueError("invalid checked time")
        except ValueError as error:
            raise ValueError("invalid checked time") from error
        if (not isinstance(self.redirect_domains, tuple) or not self.redirect_domains or len(set(self.redirect_domains)) != len(self.redirect_domains)
                or any(not isinstance(item, str) or _domain(f"https://{item}") != item for item in self.redirect_domains)
                or _domain(f"https://{self.requested_domain}") != self.requested_domain):
            raise ValueError("invalid redirect domains")
        if self.requested_domain != self.redirect_domains[0]:
            raise ValueError("invalid requested domain")
        if self.status == "unavailable":
            if self.final_domain is not None or self.content_type is not None or self.size_bytes is not None or self.reason_code not in _REASONS:
                raise ValueError("invalid unavailable result")
        elif self.final_domain is None or self.final_domain not in self.redirect_domains or self.content_type not in _MEDIA_TYPE_EXTENSIONS or not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or not 0 <= self.size_bytes <= MAX_RESPONSE_BYTES:
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
    if isinstance(error, DownloadRedirectError): return "redirect_error"
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
    workspace = Path(tempfile.mkdtemp(prefix="live-smoke-")).resolve()
    outcome: LiveSmokeResult
    try:
        result = downloader(requested_url, workspace, max_bytes=MAX_RESPONSE_BYTES, timeout=TOTAL_TIMEOUT_SECONDS)
        if not isinstance(result, DownloadResult) or not isinstance(result.path, Path):
            raise DownloadSecurityError("forged result")
        chain = tuple(result.redirect_chain)
        domains = tuple(_domain(item) for item in chain)
        try:
            file_stat = result.path.lstat()
        except OSError as error:
            raise DownloadStorageError("result file unavailable") from error
        suffix = _MEDIA_TYPE_EXTENSIONS.get(result.media_type) if isinstance(result.media_type, str) else None
        if (not chain or not all(_safe_url(item, allow_http=True) for item in chain) or not all(domains)
                or canonical_site_identity(chain[0]) != canonical_site_identity(requested_url)
                or chain[-1] != result.source_url or result.path.parent != workspace
                or result.path.resolve() != result.path or result.path.is_symlink()
                or not stat.S_ISREG(file_stat.st_mode) or not isinstance(result.size_bytes, int)
                or isinstance(result.size_bytes, bool) or not 0 <= result.size_bytes <= MAX_RESPONSE_BYTES
                or file_stat.st_size != result.size_bytes or suffix is None or result.path.suffix.casefold() != suffix):
            raise DownloadSecurityError("forged result")
        ordered = tuple(dict.fromkeys(domains))
        allowlisted = {canonical_site_identity(root) for root in official_roots}
        status = "healthy" if all(canonical_site_identity(item) in allowlisted for item in chain) else "redirect_review"
        outcome = LiveSmokeResult(province, status, requested_domain, domains[-1], ordered, timestamp, result.media_type, result.size_bytes, None if status == "healthy" else "unlisted_redirect_domain")
    except DownloadError as error:
        outcome = LiveSmokeResult(province, "unavailable", requested_domain, None, known_domains, timestamp, None, None, _reason(error))
    except (OSError, TypeError, ValueError):
        outcome = LiveSmokeResult(province, "unavailable", requested_domain, None, known_domains, timestamp, None, None, "security_rejection")
    try:
        shutil.rmtree(workspace)
    except OSError:
        try:
            shutil.rmtree(workspace, ignore_errors=True)
        except OSError:
            pass
        return LiveSmokeResult(province, "unavailable", requested_domain, None, known_domains, timestamp, None, None, "storage_error")
    return outcome


def _load_province(token: str) -> tuple[str, tuple[str, ...]]:
    discovery = load_province_catalog(_CATALOG_PATH).resolve(token)
    return discovery.province, discovery.official_roots


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("invalid arguments")


class _SingleUseAction(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace, values: object, option_string: str | None = None) -> None:
        if getattr(namespace, self.dest, None) is not None:
            raise ValueError("duplicate argument")
        setattr(namespace, self.dest, values)


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--province", required=True, action=_SingleUseAction)
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
