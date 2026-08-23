import unittest

from scripts.contracts import FactClaim, SourceCandidate, SourceTier, EvidenceStatus
from scripts.source_policy import (
    canonicalize_provenance_url,
    canonicalize_url,
    content_fingerprint,
    deduplicate_candidates,
    evaluate_claims,
)


class SourcePolicyTest(unittest.TestCase):
    """Policy tests use only complete, synthetic evidence candidates."""

    def candidate(
        self,
        source_id,
        tier=SourceTier.C,
        publisher=None,
        url=None,
        citation_root=None,
        content_hash=None,
    ):
        return SourceCandidate(
            source_id=source_id,
            url=url if url is not None else f"https://{source_id}.example.test/article",
            publisher=(
                publisher if publisher is not None else f"Publisher {source_id}"
            ),
            tier=tier,
            published_at="2026-08-01",
            retrieved_at="2026-08-23T00:00:00Z",
            content_hash=(content_hash if content_hash is not None else f"sha256:{source_id}"),
            citation_root=(
                citation_root
                if citation_root is not None
                else f"https://{source_id}.example.test/root"
            ),
            summary="Synthetic source",
        )

    def claim(self, source_id, value, *, field="min_score", unit="分"):
        return FactClaim(
            field=field,
            value=value,
            unit=unit,
            source_id=source_id,
            method="synthetic-table",
        )

    def test_canonical_url_removes_tracking_but_keeps_document_query(self):
        canonical = canonicalize_url(
            "HTTPS://Example.TEST:443/notices?id=2026-01&utm_source=newsletter&gclid=x#top"
        )
        self.assertEqual(canonical, "https://example.test/notices?id=2026-01")

    def test_content_fingerprint_is_stable_across_line_endings_and_whitespace(self):
        self.assertEqual(
            content_fingerprint("  score  588\r\nnext\tline "),
            content_fingerprint("score 588\nnext line"),
        )

    def test_same_article_on_three_urls_counts_once(self):
        reposts = [
            self.candidate("z", publisher="City News", citation_root="https://official.test/a"),
            self.candidate("a", publisher="City News", citation_root="https://official.test/a"),
            self.candidate("m", publisher="City News", citation_root="https://official.test/a"),
        ]
        unique, rejected = deduplicate_candidates(reposts)
        self.assertEqual([candidate.source_id for candidate in unique], ["a"])
        self.assertEqual(set(rejected.values()), {"same-publisher-or-citation-root"})

    def test_content_hash_match_counts_once_even_for_different_publishers(self):
        unique, rejected = deduplicate_candidates(
            [
                self.candidate("a", content_hash="sha256:identical"),
                self.candidate("b", content_hash="sha256:identical"),
            ]
        )
        self.assertEqual([candidate.source_id for candidate in unique], ["a"])
        self.assertEqual(rejected, {"b": "same-content-hash"})

    def test_same_site_variants_count_once_despite_distinct_other_identities(self):
        unique, rejected = deduplicate_candidates(
            [
                self.candidate(
                    "a",
                    publisher="Publisher A",
                    url="https://Example.TEST:443/first",
                    citation_root="https://upstream-a.test/original",
                    content_hash="sha256:a",
                ),
                self.candidate(
                    "b",
                    publisher="Publisher B",
                    url="https://example.test./second",
                    citation_root="https://upstream-b.test/original",
                    content_hash="sha256:b",
                ),
                self.candidate(
                    "c",
                    publisher="Publisher C",
                    url="https://www.example.test/third",
                    citation_root="https://upstream-c.test/original",
                    content_hash="sha256:c",
                ),
            ]
        )

        self.assertEqual([candidate.source_id for candidate in unique], ["a"])
        self.assertEqual(rejected, {"b": "same-site", "c": "same-site"})

    def test_candidates_missing_any_required_identity_fail_closed(self):
        candidates = [
            self.candidate("publisher", publisher=" "),
            self.candidate("site", url="not-an-absolute-url"),
            self.candidate("provenance", citation_root=" "),
            self.candidate("fingerprint", content_hash=" "),
        ]

        unique, rejected = deduplicate_candidates(candidates)
        fact = evaluate_claims(
            "min_score",
            [self.claim(candidate.source_id, 588) for candidate in candidates],
            candidates,
        )

        self.assertEqual(unique, [])
        self.assertEqual(
            rejected,
            {
                "fingerprint": "insufficient-source-identity",
                "provenance": "insufficient-source-identity",
                "publisher": "insufficient-source-identity",
                "site": "insufficient-source-identity",
            },
        )
        self.assertEqual(fact.status, EvidenceStatus.MISSING)
        self.assertIsNone(fact.value)

    def test_deduplication_collapses_transitive_independence_links(self):
        unique, rejected = deduplicate_candidates(
            [
                self.candidate("a", publisher="One", content_hash="sha256:a"),
                self.candidate("b", publisher="One", content_hash="sha256:shared"),
                self.candidate("c", publisher="Three", content_hash="sha256:shared"),
            ]
        )
        self.assertEqual([candidate.source_id for candidate in unique], ["a"])
        self.assertEqual(set(rejected), {"b", "c"})

    def test_three_independent_c_sources_accept_exact_value(self):
        sources = [self.candidate("s3"), self.candidate("s1"), self.candidate("s2")]
        claims = [self.claim("s1", 588), self.claim("s2", 588), self.claim("s3", 588)]
        fact = evaluate_claims("min_score", claims, sources)
        self.assertEqual(fact.status, EvidenceStatus.REFERENCE)
        self.assertEqual(fact.value, 588)
        self.assertEqual(fact.source_ids, ("s1", "s2", "s3"))
        self.assertEqual(fact.method, "three-source-consensus")

    def test_fewer_than_three_c_sources_remains_missing(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("s1", 588), self.claim("s2", 588)],
            [self.candidate("s1"), self.candidate("s2")],
        )
        self.assertEqual(fact.status, EvidenceStatus.MISSING)
        self.assertIsNone(fact.value)

    def test_conflicting_c_sources_never_average(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("s1", 588), self.claim("s2", 589), self.claim("s3", 588)],
            [self.candidate("s1"), self.candidate("s2"), self.candidate("s3")],
        )
        self.assertEqual(fact.status, EvidenceStatus.CONFLICT)
        self.assertIsNone(fact.value)

    def test_one_consistent_a_source_is_official_even_when_c_sources_disagree(self):
        fact = evaluate_claims(
            "min_score",
            [
                self.claim("official", 588),
                self.claim("c1", 587),
                self.claim("c2", 589),
                self.claim("c3", 587),
            ],
            [
                self.candidate("official", tier=SourceTier.A),
                self.candidate("c1"),
                self.candidate("c2"),
                self.candidate("c3"),
            ],
        )
        self.assertEqual(fact.status, EvidenceStatus.OFFICIAL)
        self.assertEqual(fact.value, 588)
        self.assertEqual(fact.source_ids, ("official",))

    def test_conflicting_independent_a_sources_are_conflict(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("a1", 588), self.claim("a2", 589)],
            [
                self.candidate("a1", tier=SourceTier.A),
                self.candidate("a2", tier=SourceTier.A),
            ],
        )
        self.assertEqual(fact.status, EvidenceStatus.CONFLICT)
        self.assertIsNone(fact.value)

    def test_two_traceable_independent_b_sources_are_corroborated(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("b2", 588), self.claim("b1", 588)],
            [
                self.candidate("b1", tier=SourceTier.B),
                self.candidate("b2", tier=SourceTier.B),
            ],
        )
        self.assertEqual(fact.status, EvidenceStatus.CORROBORATED)
        self.assertEqual(fact.value, 588)
        self.assertEqual(fact.source_ids, ("b1", "b2"))
        self.assertEqual(fact.method, "two-source-consensus")

    def test_only_absolute_http_provenance_urls_are_canonicalized(self):
        self.assertEqual(
            canonicalize_provenance_url(
                "HTTPS://Official.TEST:443/notices?id=1&utm_source=repost#section"
            ),
            "https://official.test/notices?id=1",
        )
        for invalid in (
            "opaque-root-label",
            "/relative/upstream",
            "ftp://official.test/file",
            "https://user:password@official.test/file",
            "https:///missing-host",
            "https://bad_host.test/file",
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(canonicalize_provenance_url(invalid), "")

    def test_explicit_empty_ports_are_invalid_without_rejecting_real_ports(self):
        for invalid in (
            "https://alpha.test:/original",
            "https://[2001:db8::1]:/original",
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(canonicalize_provenance_url(invalid), "")

        self.assertEqual(
            canonicalize_provenance_url("https://alpha.test/original"),
            "https://alpha.test/original",
        )
        self.assertEqual(
            canonicalize_provenance_url("https://alpha.test:8443/original"),
            "https://alpha.test:8443/original",
        )
        self.assertEqual(
            canonicalize_provenance_url("https://alpha.test:443/original"),
            "https://alpha.test/original",
        )
        self.assertEqual(
            canonicalize_provenance_url("https://[2001:db8::1]/original"),
            "https://[2001:db8::1]/original",
        )

    def test_opaque_b_citation_roots_cannot_be_corroborated(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("b1", 588), self.claim("b2", 588)],
            [
                self.candidate("b1", tier=SourceTier.B, citation_root="opaque-root-one"),
                self.candidate("b2", tier=SourceTier.B, citation_root="opaque-root-two"),
            ],
        )

        self.assertEqual(fact.status, EvidenceStatus.MISSING)
        self.assertIsNone(fact.value)

    def test_empty_port_b_citation_roots_cannot_be_corroborated(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("b1", 588), self.claim("b2", 588)],
            [
                self.candidate(
                    "b1",
                    tier=SourceTier.B,
                    citation_root="https://alpha.test:/original",
                ),
                self.candidate(
                    "b2",
                    tier=SourceTier.B,
                    citation_root="https://beta.test:/original",
                ),
            ],
        )

        self.assertEqual(fact.status, EvidenceStatus.MISSING)
        self.assertIsNone(fact.value)

    def test_b_sources_without_citation_roots_cannot_be_corroborated(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("b1", 588), self.claim("b2", 588)],
            [
                self.candidate("b1", tier=SourceTier.B, citation_root=""),
                self.candidate("b2", tier=SourceTier.B, citation_root=""),
            ],
        )
        self.assertEqual(fact.status, EvidenceStatus.MISSING)
        self.assertIsNone(fact.value)

    def test_invalid_b_provenance_is_excluded_before_conflict_policy(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("b1", 588), self.claim("b2", 589)],
            [
                self.candidate("b1", tier=SourceTier.B),
                self.candidate("b2", tier=SourceTier.B, citation_root=""),
            ],
        )
        self.assertEqual(fact.status, EvidenceStatus.MISSING)
        self.assertIsNone(fact.value)

    def test_b_claim_traced_to_registered_a_upstream_is_official(self):
        official = self.candidate(
            "a1", tier=SourceTier.A, url="https://official.test/notices?id=1"
        )
        b_source = self.candidate(
            "b1",
            tier=SourceTier.B,
            citation_root="https://official.test/notices?id=1&utm_source=repost",
        )
        fact = evaluate_claims("min_score", [self.claim("b1", 588)], [official, b_source])
        self.assertEqual(fact.status, EvidenceStatus.OFFICIAL)
        self.assertEqual(fact.value, 588)
        self.assertEqual(fact.source_ids, ("b1",))

    def test_a_representative_survives_same_root_b_repost_with_earlier_id(self):
        shared_root = "https://official.test/notices?id=1"
        official = self.candidate(
            "z-official",
            tier=SourceTier.A,
            url=shared_root,
            citation_root=shared_root,
        )
        repost = self.candidate(
            "a-repost",
            tier=SourceTier.B,
            citation_root=shared_root,
        )
        unique, _ = deduplicate_candidates([repost, official])
        fact = evaluate_claims("min_score", [self.claim("a-repost", 588)], [repost, official])
        self.assertEqual([source.source_id for source in unique], ["z-official"])
        self.assertEqual(fact.status, EvidenceStatus.OFFICIAL)
        self.assertEqual(fact.value, 588)
        self.assertEqual(fact.source_ids, ("z-official",))

    def test_two_traceable_b_claims_can_corroborate_despite_untraceable_agreement(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("b1", 588), self.claim("b2", 588), self.claim("b3", 588)],
            [
                self.candidate("b1", tier=SourceTier.B),
                self.candidate("b2", tier=SourceTier.B),
                self.candidate("b3", tier=SourceTier.B, citation_root=""),
            ],
        )
        self.assertEqual(fact.status, EvidenceStatus.CORROBORATED)
        self.assertEqual(fact.value, 588)
        self.assertEqual(fact.source_ids, ("b1", "b2"))

    def test_rejected_source_claim_is_not_counted_toward_consensus(self):
        fact = evaluate_claims(
            "min_score",
            [self.claim("s1", 588), self.claim("s2", 588), self.claim("s3", 588)],
            [
                self.candidate("s1", publisher="One"),
                self.candidate("s2", publisher="One"),
                self.candidate("s3", publisher="Three"),
            ],
        )
        self.assertEqual(fact.status, EvidenceStatus.MISSING)
        self.assertIsNone(fact.value)

    def test_fact_identity_is_deterministic_for_permuted_input(self):
        sources = [self.candidate("s1"), self.candidate("s2"), self.candidate("s3")]
        claims = [self.claim("s1", 588), self.claim("s2", 588), self.claim("s3", 588)]
        first = evaluate_claims("min_score", claims, sources)
        second = evaluate_claims("min_score", list(reversed(claims)), list(reversed(sources)))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
