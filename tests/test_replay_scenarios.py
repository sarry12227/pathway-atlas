"""Deterministic, offline replays for synthetic evidence orchestration seams."""

from __future__ import annotations

import ast
import base64
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import UUID


_SENTINEL_MARKER = "REPLAY-NETWORK-BLOCKED"


def _blocked_network(*_args: object, **_kwargs: object) -> object:
    raise AssertionError(_SENTINEL_MARKER)


class _BlockedSocket:
    connect = _blocked_network
    connect_ex = _blocked_network
    send = _blocked_network
    sendall = _blocked_network
    sendto = _blocked_network

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass


def _install_network_sentinel() -> None:
    socket.getaddrinfo = _blocked_network  # type: ignore[assignment]
    socket.gethostbyname = _blocked_network  # type: ignore[assignment]
    socket.gethostbyaddr = _blocked_network  # type: ignore[assignment]
    socket.getfqdn = _blocked_network  # type: ignore[assignment]
    socket.create_connection = _blocked_network  # type: ignore[assignment]
    socket.socket = _BlockedSocket  # type: ignore[assignment]


@contextmanager
def _network_sentinel():
    """Block the complete child sentinel surface in one parent-test scope."""
    with ExitStack() as patches:
        patches.enter_context(mock.patch.object(socket, "getaddrinfo", _blocked_network))
        patches.enter_context(mock.patch.object(socket, "gethostbyname", _blocked_network))
        patches.enter_context(mock.patch.object(socket, "gethostbyaddr", _blocked_network))
        patches.enter_context(mock.patch.object(socket, "getfqdn", _blocked_network))
        patches.enter_context(mock.patch.object(socket, "create_connection", _blocked_network))
        patches.enter_context(mock.patch.object(socket, "socket", _BlockedSocket))
        yield


def _safe_qr_dns(hostname: str, port: int, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
    """Offline resolver seam for the one synthetic public QR host."""
    if hostname != "qr-origin.example.test":
        raise AssertionError(_SENTINEL_MARKER)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


def _assert_network_sentinel_canaries() -> None:
    for call in (
        lambda: socket.getaddrinfo("example.test", 443),
        lambda: socket.gethostbyname("example.test"),
        lambda: socket.gethostbyaddr("8.8.8.8"),
        lambda: socket.getfqdn("example.test"),
        lambda: socket.create_connection(("example.test", 443)),
    ):
        try:
            call()
        except AssertionError as error:
            if str(error) != _SENTINEL_MARKER:
                raise
        else:
            raise AssertionError("network sentinel canary was not blocked")
    blocked = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for call in (
        lambda: blocked.connect(("127.0.0.1", 9)),
        lambda: blocked.connect_ex(("127.0.0.1", 9)),
        lambda: blocked.send(b"x"),
        lambda: blocked.sendall(b"x"),
        lambda: blocked.sendto(b"x", ("127.0.0.1", 9)),
    ):
        try:
            call()
        except AssertionError as error:
            if str(error) != _SENTINEL_MARKER:
                raise
        else:
            raise AssertionError("network sentinel canary was not blocked")


if os.environ.get("REPLAY_CHILD_SENTINEL") == "1":
    _install_network_sentinel()

from scripts.contracts import (
    CapabilityReport,
    CapabilityTier,
    EvidenceFact,
    EvidenceStatus,
    FactClaim,
    OrdinaryBatchPolicy,
    RecommendationResult,
    SourceCandidate,
    SourceTier,
)
from scripts.evidence import EvidenceStore
from scripts.report_model import StudentProfile, build_report_model, render_markdown
from scripts.adapters import CellStatus, ColumnMapping, ExtractedCoverage, ExtractedRow, ExtractedTable
from scripts.adapters import qr as qr_adapter
from scripts.adapters.ocr_rows import normalize_ocr_rows
from scripts.downloader import DownloadResult
from scripts.source_policy import deduplicate_candidates, evaluate_claims
from scripts.validate_evidence import validate_bundle_snapshot


FIXTURES = Path(__file__).parent / "fixtures" / "replay"
SCENARIOS = (
    "heilongjiang-qr",
    "shanghai-masked-ocr",
    "joy-report-crosscheck",
)
_ROOT_KEYS = frozenset({"schema_version", "scenario_id", "capability", "extraction", "candidates", "claims", "expected"})
_CANDIDATE_KEYS = frozenset({"source_id", "url", "publisher", "tier", "published_at", "retrieved_at", "content_hash", "citation_root", "summary"})
_CLAIM_KEYS = frozenset({"field", "value", "unit", "source_id", "method"})
_EXPECTED_KEYS = frozenset({"fact_id", "field", "status", "value", "unit", "method", "notes", "year", "extraction_method", "locator", "candidate_count", "independent_source_count", "rejections", "report_contains", "coverage_status"})


class ReplayFixtureError(ValueError):
    """A synthetic replay input is malformed or unsafe to replay."""


def _reject_constant(_value: str) -> None:
    raise ReplayFixtureError("non-finite JSON values are not allowed")


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReplayFixtureError("duplicate JSON keys are not allowed")
        result[key] = value
    return result


def _strict_json(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", "strict")
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReplayFixtureError) as error:
        raise ReplayFixtureError("replay fixture is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ReplayFixtureError("replay fixture root must be an object")
    return value


def _require_keys(value: object, keys: frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReplayFixtureError(f"{label} has an unsupported shape")
    return value


def _candidate(value: object) -> SourceCandidate:
    item = _require_keys(value, _CANDIDATE_KEYS, "candidate")
    try:
        return SourceCandidate(
            source_id=item["source_id"], url=item["url"], publisher=item["publisher"],
            tier=SourceTier(item["tier"]), published_at=item["published_at"],
            retrieved_at=item["retrieved_at"], content_hash=item["content_hash"],
            citation_root=item["citation_root"], summary=item["summary"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ReplayFixtureError("candidate contract is invalid") from error


def _claim(value: object) -> FactClaim:
    item = _require_keys(value, _CLAIM_KEYS, "claim")
    try:
        return FactClaim(field=item["field"], value=item["value"], unit=item["unit"], source_id=item["source_id"], method=item["method"])
    except (KeyError, TypeError, ValueError) as error:
        raise ReplayFixtureError("claim contract is invalid") from error


def _policy() -> OrdinaryBatchPolicy:
    return OrdinaryBatchPolicy(
        schema_version="1.0", policy_id="replay-policy-v1", basis_id="replay-policy-basis-v1",
        search_delta_min=-10, search_delta_max=10, challenge_delta_lt=-3,
        stable_delta_le=3, tier_caps={"冲": 1, "稳": 1, "保": 1},
    )


@dataclass(frozen=True)
class ReplayResult:
    scenario_id: str
    fact: EvidenceFact
    independent_source_count: int
    rejections: tuple[tuple[str, str, str], ...]
    manifest_hash: str
    snapshot_summary: tuple[int, int, int]
    report: str

    def semantic_json(self) -> str:
        return json.dumps({
            "scenario_id": self.scenario_id, "fact": self.fact.to_dict(),
            "independent_source_count": self.independent_source_count,
            "rejections": [list(item) for item in self.rejections],
            "manifest_hash": self.manifest_hash, "snapshot_summary": list(self.snapshot_summary),
            "report": self.report,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _extraction_claims(scenario_id: str, extraction: dict[str, object], candidates: tuple[SourceCandidate, ...], output_root: Path) -> tuple[tuple[FactClaim, ...], dict[str, object]]:
    """Build claims and logical provenance from actual adapter result contracts."""
    kind = extraction["kind"]
    artifact = extraction["artifact"]
    if not isinstance(artifact, dict):
        raise ReplayFixtureError("extraction artifact must be an object")
    if scenario_id == "heilongjiang-qr":
        if kind != "xlsx-worksheet" or set(artifact) != {"qr", "worksheet"}:
            raise ReplayFixtureError("QR worksheet artifact is invalid")
        qr = artifact["qr"]
        if not isinstance(qr, dict) or set(qr) != {"host_decoded_url", "qr_image_source_id", "downloaded_file_id"}:
            raise ReplayFixtureError("QR download artifact is invalid")
        worksheet = artifact["worksheet"]
        if not isinstance(worksheet, dict) or set(worksheet) != {"sheet", "values", "cell_status", "locator", "coverage"}:
            raise ReplayFixtureError("worksheet result is invalid")
        row = ExtractedRow(worksheet["values"], worksheet["cell_status"], worksheet["locator"], 1.0)
        table = ExtractedTable("sheet:" + str(worksheet["sheet"]), None, worksheet["sheet"], (row,), ExtractedCoverage(**worksheet["coverage"]), (), "xlsx-worksheet")
        if table.extraction_method != kind or row.cell_status.get("score") is not CellStatus.EXACT:
            raise ReplayFixtureError("worksheet artifact is not an exact XLSX result")
        requested_file_id = qr["downloaded_file_id"]
        if not isinstance(requested_file_id, str):
            raise ReplayFixtureError("QR artifact downloaded file identity is invalid")
        downloaded_path = (output_root / requested_file_id).resolve()
        if downloaded_path.parent != output_root.resolve():
            raise ReplayFixtureError("QR artifact downloaded file identity is unsafe")
        downloaded_path.write_bytes(b"synthetic offline XLSX replay\n")
        download = DownloadResult(
            path=downloaded_path,
            source_url=str(qr["host_decoded_url"]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=downloaded_path.stat().st_size,
            redirect_chain=(str(qr["host_decoded_url"]),),
        )
        try:
            with mock.patch.object(socket, "getaddrinfo", _safe_qr_dns), mock.patch.object(qr_adapter, "download_public_file", return_value=download):
                resolution = qr_adapter.resolve_qr_payload(
                    qr["host_decoded_url"], output_root.resolve(), qr_image_source_id=qr["qr_image_source_id"], max_bytes=1024, timeout=1.0,
                )
        except (qr_adapter.QrPayloadError, qr_adapter.QrResolutionError, TypeError, ValueError, OSError):
            raise ReplayFixtureError("QR artifact did not produce authenticated provenance") from None
        if resolution.downloaded_file_id != qr["downloaded_file_id"]:
            raise ReplayFixtureError("QR artifact downloaded file identity is inconsistent")
        locator = f"{row.location}; qr-source={resolution.qr_image_source_id}; file={resolution.downloaded_file_id}"
        notes = f"worksheet provenance from authenticated QR source {resolution.qr_image_source_id}: {locator}"
        return tuple(FactClaim("synthetic_admission_score", row.values["score"], "synthetic-points", item.source_id, table.extraction_method) for item in candidates), {"method": table.extraction_method, "locator": locator, "value": row.values["score"], "coverage": table.coverage.to_dict(), "notes": notes}
    if scenario_id == "shanghai-masked-ocr":
        if kind != "host-ocr-rows" or set(artifact) != {"document", "mapping", "score_scale", "min_exact_confidence", "target_field"}:
            raise ReplayFixtureError("OCR artifact is invalid")
        source = output_root / "ocr-artifact.json"
        source.write_text(json.dumps(artifact["document"], ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
        table = normalize_ocr_rows(source.resolve(), ColumnMapping(artifact["mapping"], roles={artifact["target_field"]: "score"}, score_scale=artifact["score_scale"]), score_scale=artifact["score_scale"], min_exact_confidence=artifact["min_exact_confidence"])
        masked = [row for row in table.rows if row.cell_status.get(artifact["target_field"]) is CellStatus.MASKED]
        if len(masked) != 1:
            raise ReplayFixtureError("OCR artifact must preserve exactly one masked field")
        row = masked[0]
        return (), {"method": table.extraction_method, "locator": row.cell_locations[artifact["target_field"]], "value": row.values[artifact["target_field"]], "coverage": table.coverage.to_dict()}
    if scenario_id == "joy-report-crosscheck":
        if kind != "manual-structured" or set(artifact) != {"field", "value", "unit", "locator"}:
            raise ReplayFixtureError("crosscheck artifact is invalid")
        return tuple(FactClaim(artifact["field"], artifact["value"], artifact["unit"], item.source_id, kind) for item in candidates if item.source_id in {"j01", "j06"}), {"method": kind, "locator": artifact["locator"], "value": artifact["value"], "coverage": extraction["coverage"]}
    raise ReplayFixtureError("unknown extraction scenario")


def replay_scenario(scenario_id: str, output_root: Path, *, fixture_path: Path | None = None) -> ReplayResult:
    """Replay immutable JSON through public policy, evidence, validation and report seams."""

    if scenario_id not in SCENARIOS:
        raise ReplayFixtureError("unknown replay scenario")
    fixture = _strict_json(fixture_path or FIXTURES / scenario_id / "scenario.json")
    _require_keys(fixture, _ROOT_KEYS, "scenario")
    if fixture["schema_version"] != 1 or fixture["scenario_id"] != scenario_id:
        raise ReplayFixtureError("scenario identity is invalid")
    expected = _require_keys(fixture["expected"], _EXPECTED_KEYS, "expected")
    capability_value = fixture["capability"]
    if not isinstance(capability_value, dict) or set(capability_value) != {"tier", "degradations"}:
        raise ReplayFixtureError("capability is invalid")
    extraction = fixture["extraction"]
    if not isinstance(extraction, dict) or set(extraction) != {"kind", "artifact", "coverage"}:
        raise ReplayFixtureError("extraction is invalid")
    if not isinstance(fixture["candidates"], list) or not isinstance(fixture["claims"], list):
        raise ReplayFixtureError("candidate and claim collections are required")
    candidates = tuple(_candidate(item) for item in fixture["candidates"])
    output_root.mkdir(parents=True, exist_ok=True)
    claims, derived = _extraction_claims(scenario_id, extraction, candidates, output_root)
    if len({item.source_id for item in candidates}) != len(candidates):
        raise ReplayFixtureError("candidate identifiers must be unique")
    if any(claim.source_id not in {item.source_id for item in candidates} for claim in claims):
        raise ReplayFixtureError("claims must reference candidates")
    if not isinstance(expected["rejections"], list):
        raise ReplayFixtureError("expected rejections must be an ordered list")
    if len(candidates) != expected["candidate_count"]:
        raise ReplayFixtureError("candidate count does not match fixture expectation")

    kept, public_reasons = deduplicate_candidates(candidates)
    retained = tuple(kept)
    by_id = {item.source_id: item for item in candidates}
    rejections = tuple(tuple(item) for item in expected["rejections"])
    for source_id, retained_id, reason in rejections:
        pair, pair_reasons = deduplicate_candidates((by_id[source_id], by_id[retained_id]))
        if tuple(item.source_id for item in pair) != (retained_id,) or public_reasons.get(source_id) != reason:
            raise ReplayFixtureError("public dedupe contract does not prove the fixture mapping")
    if len(retained) != expected["independent_source_count"]:
        raise ReplayFixtureError("independent source count does not match fixture expectation")
    policy_fact = evaluate_claims(str(expected["field"]), claims, candidates)
    status = EvidenceStatus(expected["status"])
    if status in {EvidenceStatus.REFERENCE, EvidenceStatus.MISSING}:
        fact = policy_fact
    else:
        fact = EvidenceFact(
            fact_id=str(expected["fact_id"]), field=str(expected["field"]), value=expected["value"],
            unit=expected["unit"], status=status, source_ids=tuple(item.source_id for item in retained),
            method=str(expected["method"]), notes=str(expected["notes"]),
        )
    if derived["method"] != expected["extraction_method"] or derived["locator"] != expected["locator"]:
        raise ReplayFixtureError("extraction provenance does not match the adapter result")
    expected_notes = str(expected["notes"])
    derived_notes = str(derived.get("notes", expected_notes))
    if derived_notes != expected_notes:
        raise ReplayFixtureError("extraction notes do not match authenticated provenance")
    if status is EvidenceStatus.MASKED and (derived["value"] is not None or any(character.isdigit() for character in str(expected["unit"]) + str(expected["notes"]))):
        raise ReplayFixtureError("masked artifact cannot carry an exact boundary")
    if (fact.status.value, fact.value, fact.unit, fact.method) != (expected["status"], expected["value"], expected["unit"], expected["method"]):
        raise ReplayFixtureError("policy result does not match fixture expectation")
    if fact.fact_id != expected["fact_id"] or fact.notes != derived_notes:
        fact = EvidenceFact(str(expected["fact_id"]), fact.field, fact.value, fact.unit, fact.status, fact.source_ids, fact.method, derived_notes)

    report = CapabilityReport(
        tier=CapabilityTier(capability_value["tier"]), degradations=tuple(capability_value["degradations"]),
        missing_capabilities=("browse", "search", "vision", "docx", "openpyxl", "pdfplumber"), python_version="3.10.0",
    )
    with mock.patch("scripts.evidence.uuid4", return_value=UUID("01234567-89ab-cdef-0123-456789abcdef")):
        store = EvidenceStore.create(output_root.resolve(), report)
    for item in retained:
        store.add_candidate(item)
    for source_id, retained_id, reason in rejections:
        store.reject_candidate(source_id, reason)
        store.add_context({"kind": "replay-dedupe", "source_id": source_id, "retained_source_id": retained_id, "reason": reason})
    store.add_fact(fact, year=expected["year"], extraction_method=expected["extraction_method"], locator=expected["locator"])
    manifest = store.finalize()
    validation = validate_bundle_snapshot(store.session_path)
    if validation.snapshot is None:
        raise ReplayFixtureError("authenticated replay validation failed")
    recommendations = RecommendationResult(
        ordinary_batch_policy=_policy(), input_years=(expected["year"],), usable_years=(),
        coverage_status=EvidenceStatus(expected["coverage_status"]), empty_reason="unusable_evidence" if status is EvidenceStatus.MASKED else "missing_verified_coverage",
        warnings=(str(extraction["coverage"]), f"replay evidence source IDs: {'、'.join(fact.source_ids)}"),
    )
    model = build_report_model(
        StudentProfile("演示省", "3+1+2", "物理", ("化学",), 1000, "高三", int(expected["year"])),
        recommendations, None, None, validation.snapshot,
    )
    markdown = render_markdown(model)
    for fragment in expected["report_contains"]:
        if fragment not in markdown:
            raise ReplayFixtureError("report projection omitted required disclosure")
    provenance_count = sum(
        json.loads(line).get("kind") == "fact-provenance"
        for line in (store.session_path / "context.jsonl").read_text("utf-8").splitlines()
    )
    return ReplayResult(scenario_id, fact, len(retained), rejections, manifest.manifest_hash, (len(validation.snapshot.facts), len(validation.snapshot.rejections), provenance_count), markdown)


def _fixture_hashes() -> dict[str, str]:
    return {str(path.relative_to(FIXTURES)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(FIXTURES.glob("*/*.json"))}


class ReplayScenarioTest(unittest.TestCase):
    def test_replay_helper_uses_no_private_source_policy_api(self):
        module = ast.parse(Path(__file__).read_text("utf-8"))
        imports = [name.name for item in ast.walk(module) if isinstance(item, ast.ImportFrom) and item.module == "scripts.source_policy" for name in item.names]
        self.assertTrue(all(not name.startswith("_") for name in imports))

    def test_extraction_artifacts_are_structured_and_masked_mutation_fails_closed(self):
        for scenario_id in ("heilongjiang-qr", "shanghai-masked-ocr"):
            fixture = _strict_json(FIXTURES / scenario_id / "scenario.json")
            self.assertIsInstance(fixture["extraction"]["artifact"], dict)
        fixture = _strict_json(FIXTURES / "shanghai-masked-ocr" / "scenario.json")
        self.assertIn("document", fixture["extraction"]["artifact"])

    def test_committed_scenarios_exist_with_exact_ids(self):
        self.assertTrue(all((FIXTURES / scenario_id).is_dir() for scenario_id in SCENARIOS))
        for scenario_id in SCENARIOS:
            fixture = _strict_json(FIXTURES / scenario_id / "scenario.json")
            self.assertEqual(fixture["scenario_id"], scenario_id)

    def test_strict_fixture_boundaries_fail_closed_and_are_path_neutral(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, payload in {
                "duplicate": '{"x":1,"x":2}', "nonfinite": '{"x":NaN}',
                "malformed": '{', "unknown": '{"schema_version":1}',
            }.items():
                path = root / f"{name}.json"; path.write_text(payload, encoding="utf-8", newline="\n")
                with self.assertRaises(ReplayFixtureError) as raised:
                    if name == "unknown":
                        _require_keys(_strict_json(path), _ROOT_KEYS, "scenario")
                    else:
                        _strict_json(path)
                self.assertNotIn(str(root), str(raised.exception))

    def test_qr_spreadsheet_reference_preserves_worksheet_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = replay_scenario("heilongjiang-qr", Path(temporary))
        self.assertEqual(result.fact.status, EvidenceStatus.REFERENCE)
        self.assertEqual((result.fact.value, result.fact.unit), (620, "synthetic-points"))
        self.assertEqual(result.independent_source_count, 3)
        self.assertEqual(result.snapshot_summary, (1, 0, 1))
        self.assertIn("worksheet", result.fact.notes)
        self.assertIn("synthetic.xlsx", result.fact.notes)
        self.assertIn("qr-c1", result.report)

    def test_qr_payload_and_downloaded_file_mutations_fail_closed(self):
        fixture = _strict_json(FIXTURES / "heilongjiang-qr" / "scenario.json")
        with tempfile.TemporaryDirectory() as temporary:
            baseline = replay_scenario("heilongjiang-qr", Path(temporary)).semantic_json()
        changed = json.loads(json.dumps(fixture))
        changed["extraction"]["artifact"]["qr"]["downloaded_file_id"] = "alternate.xlsx"
        changed["expected"]["notes"] = "worksheet provenance from authenticated QR source synthetic-qr-image: synthetic-sheet!A2:F2; qr-source=synthetic-qr-image; file=alternate.xlsx"
        changed["expected"]["locator"] = "synthetic-sheet!A2:F2; qr-source=synthetic-qr-image; file=alternate.xlsx"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "changed.json"
            path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8", newline="\n")
            self.assertNotEqual(baseline, replay_scenario("heilongjiang-qr", root / "output", fixture_path=path).semantic_json())
        for mutation in (
            ("host_decoded_url", "file:///synthetic.xlsx"),
            ("host_decoded_url", "C:/synthetic.xlsx"),
            ("downloaded_file_id", "file:///synthetic.xlsx"),
            ("downloaded_file_id", "C:/synthetic.xlsx"),
        ):
            with self.subTest(mutation=mutation):
                mutated = json.loads(json.dumps(fixture))
                mutated["extraction"]["artifact"]["qr"][mutation[0]] = mutation[1]
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    path = root / "mutated.json"
                    path.write_text(json.dumps(mutated, ensure_ascii=False), encoding="utf-8", newline="\n")
                    with self.assertRaises(ReplayFixtureError) as raised:
                        replay_scenario("heilongjiang-qr", root / "output", fixture_path=path)
                self.assertNotIn(str(root), str(raised.exception))

    def test_parent_network_sentinel_covers_dns_tcp_and_udp(self):
        with _network_sentinel():
            _assert_network_sentinel_canaries()

    def test_masked_ocr_never_creates_an_exact_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = replay_scenario("shanghai-masked-ocr", Path(temporary))
        self.assertEqual(result.fact.status, EvidenceStatus.MASKED)
        self.assertIsNone(result.fact.value)
        self.assertNotRegex(result.fact.unit or "", r"\d")
        self.assertIn("屏蔽", result.report)
        self.assertNotIn("N以上=", result.report)

    def test_exact_masked_boundary_mutation_fails_closed_without_path_echo(self):
        fixture = _strict_json(FIXTURES / "shanghai-masked-ocr" / "scenario.json")
        fixture["extraction"]["artifact"]["document"]["rows"][1]["cells"][1]["raw_text"] = "N以上=999"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "mutated.json"
            path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8", newline="\n")
            with self.assertRaises(ReplayFixtureError) as raised:
                replay_scenario("shanghai-masked-ocr", root / "output", fixture_path=path)
            self.assertNotIn(str(root), str(raised.exception))

    def test_ten_reposts_collapse_to_two_then_remain_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = replay_scenario("joy-report-crosscheck", Path(temporary))
        self.assertEqual(result.independent_source_count, 2)
        self.assertEqual(len(result.rejections), 8)
        self.assertEqual(result.fact.status, EvidenceStatus.MISSING)
        self.assertEqual(result.fact.method, "insufficient-independent-sources")
        self.assertIsNone(result.fact.value)

    def test_identity_mutation_canaries_change_dedupe_direction(self):
        fixture = _strict_json(FIXTURES / "joy-report-crosscheck" / "scenario.json")
        candidates = [_candidate(item) for item in fixture["candidates"]]
        baseline, _ = deduplicate_candidates(candidates)
        self.assertEqual(len(baseline), 2)
        for source_id, attribute, replacement in (("j02", "publisher", "fresh-publisher"), ("j03", "url", "https://fresh-site.example.test/a"), ("j04", "citation_root", "https://fresh-root.example.test/a"), ("j05", "content_hash", "sha256:" + "f" * 64)):
            mutated = list(candidates)
            index = next(index for index, item in enumerate(mutated) if item.source_id == source_id)
            original = mutated[index]
            values = original.to_dict(); values[attribute] = replacement
            mutated[index] = SourceCandidate(**{**values, "tier": SourceTier(values["tier"])})
            kept, _ = deduplicate_candidates(mutated)
            with self.subTest(source_id=source_id, identity=attribute):
                self.assertEqual(len(kept), 3)

    def test_replay_is_offline_and_never_mutates_committed_input(self):
        before = _fixture_hashes()
        with tempfile.TemporaryDirectory() as temporary, _network_sentinel():
            for scenario_id in SCENARIOS:
                replay_scenario(scenario_id, Path(temporary) / scenario_id)
            with self.assertRaisesRegex(AssertionError, f"^{_SENTINEL_MARKER}$"):
                socket.getaddrinfo("example.test", 443)
        self.assertEqual(_fixture_hashes(), before)

    def test_replays_are_byte_identical_across_runs_and_hash_seeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = replay_scenario("heilongjiang-qr", root / "one")
            second = replay_scenario("heilongjiang-qr", root / "two")
        self.assertEqual(first.semantic_json(), second.semantic_json())
        child_outputs = []
        for seed in ("1", "8675309"):
            environment = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8", REPLAY_CHILD_SENTINEL="1")
            completed = subprocess.run([sys.executable, "-m", "tests.test_replay_scenarios", "--replay-child"], cwd=Path(__file__).parents[1], env=environment, capture_output=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
            self.assertIn(_SENTINEL_MARKER.encode("ascii"), completed.stdout)
            child_outputs.append(completed.stdout)
        self.assertEqual(child_outputs[0], child_outputs[1])

    def test_test_helper_result_is_frozen_and_json_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = replay_scenario("heilongjiang-qr", Path(temporary))
        with self.assertRaises((AttributeError, TypeError)):
            result.scenario_id = "mutated"  # type: ignore[misc]
        self.assertEqual(json.loads(result.semantic_json())["scenario_id"], "heilongjiang-qr")
        self.assertFalse(any(math.isnan(value) for value in (result.independent_source_count,)))

    def test_safe_controls_preserve_consensus_masked_display_and_two_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            qr = replay_scenario("heilongjiang-qr", Path(temporary) / "qr")
            masked = replay_scenario("shanghai-masked-ocr", Path(temporary) / "ocr")
            crosscheck = replay_scenario("joy-report-crosscheck", Path(temporary) / "joy")
        self.assertEqual(qr.fact.status, EvidenceStatus.REFERENCE)
        self.assertEqual(masked.fact.status, EvidenceStatus.MASKED)
        self.assertIn("屏蔽", masked.report)
        self.assertEqual(crosscheck.independent_source_count, 2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--replay-child"]:
        with tempfile.TemporaryDirectory() as temporary:
            result = replay_scenario("heilongjiang-qr", Path(temporary))
        _assert_network_sentinel_canaries()
        print(json.dumps({"marker": _SENTINEL_MARKER, "semantic": result.semantic_json(), "markdown_b64": base64.b64encode(result.report.encode("utf-8")).decode("ascii")}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        unittest.main()
