from __future__ import annotations

from datetime import date
import ipaddress
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit
import unittest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "references" / "provinces" / "index.json"
README_PATH = ROOT / "references" / "provinces" / "README.md"
SCHEMA_PATH = ROOT / "schemas" / "province-catalog.schema.json"
TASK_DATE = date(2026, 8, 24)
MAX_ALIASES_PER_PROVINCE = 3

EXPECTED_ORDER = (
    "北京",
    "天津",
    "上海",
    "浙江",
    "山东",
    "海南",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "安徽",
    "福建",
    "江西",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
)

EXPECTED_MODES = {
    **{province: "3+3" for province in EXPECTED_ORDER[:6]},
    **{province: "3+1+2" for province in EXPECTED_ORDER[6:]},
}

EXPECTED_AUTHORITIES_AND_ROOTS = {
    "北京": ("北京教育考试院", ("https://www.bjeea.cn/",)),
    "天津": ("天津市教育招生考试院", ("https://jy.tj.gov.cn/",)),
    "上海": ("上海市教育考试院", ("https://www.shmeea.edu.cn/",)),
    "浙江": ("浙江省教育考试院", ("https://www.zjzs.net/",)),
    "山东": ("山东省教育招生考试院", ("https://www.sdzk.cn/",)),
    "海南": ("海南省考试局", ("https://ea.hainan.gov.cn/",)),
    "河北": ("河北省教育考试院", ("https://www.hebeea.edu.cn/",)),
    "山西": ("山西省招生考试管理中心", ("https://jyt.shanxi.gov.cn/",)),
    "内蒙古": ("内蒙古自治区教育考试院", ("https://www.nm.zsks.cn/",)),
    "辽宁": ("辽宁省高中等教育招生考试委员会办公室", ("https://www.lnzsks.com/",)),
    "吉林": ("吉林省教育考试院", ("https://www.jleea.com.cn/",)),
    "黑龙江": ("黑龙江省招生考试院", ("https://www.hljea.org.cn/",)),
    "江苏": ("江苏省教育考试院", ("https://www.jseea.cn/",)),
    "安徽": ("安徽省教育招生考试院", ("https://www.ahzsks.cn/",)),
    "福建": ("福建省教育考试院", ("https://www.eeafj.cn/",)),
    "江西": ("江西省教育考试院", ("https://jyt.jiangxi.gov.cn/",)),
    "河南": ("河南省教育考试院", ("https://www.haeea.cn/",)),
    "湖北": ("湖北省教育考试院", ("https://www.hbea.edu.cn/",)),
    "湖南": ("湖南省教育考试院", ("https://jyt.hunan.gov.cn/jyt/sjyt/hnsjyksy/",)),
    "广东": ("广东省教育考试院", ("https://eea.gd.gov.cn/",)),
    "广西": ("广西壮族自治区招生考试院", ("https://www.gxeea.cn/",)),
    "重庆": ("重庆市教育考试院", ("https://www.cqksy.cn/",)),
    "四川": ("四川省教育考试院", ("https://www.sceea.cn/",)),
    "贵州": ("贵州省招生考试院", ("https://zsksy.guizhou.gov.cn/",)),
    "云南": ("云南省招生考试院", ("https://www.ynzs.cn/",)),
    "陕西": ("陕西省教育考试院", ("https://www.sneea.cn/",)),
    "甘肃": ("甘肃省教育考试院", ("https://www.ganseea.cn/",)),
    "青海": ("青海省教育招生考试院", ("https://www.qhjyks.com/",)),
    "宁夏": ("宁夏教育考试院", ("https://www.nxjyks.cn/",)),
}

EXPECTED_MODE_AUTHORITIES = {
    "https://hudong.moe.gov.cn/jyb_xwfb/xw_zt/moe_357/2026/2026_zt08/mtbd/202606/t20260608_1439867.html",
    "https://hudong.moe.gov.cn/jyb_xwfb/xw_zt/moe_357/2024/2024_zt12/wd/gkwd_zlhz/202406/t20240603_1133733.html",
    "https://hudong.moe.gov.cn/jyb_xxgk/xxgk_jyta/jyta_xueshengsi/201911/t20191126_409732.html",
    "https://hudong.moe.gov.cn/jyb_xwfb/s5147/202109/t20210916_563605.html",
}
MODE_SOURCE_2019 = (
    "https://hudong.moe.gov.cn/jyb_xxgk/xxgk_jyta/jyta_xueshengsi/"
    "201911/t20191126_409732.html"
)
MODE_SOURCE_2021 = (
    "https://hudong.moe.gov.cn/jyb_xwfb/s5147/202109/"
    "t20210916_563605.html"
)
MODE_SOURCE_2024 = (
    "https://hudong.moe.gov.cn/jyb_xwfb/xw_zt/moe_357/2024/2024_zt12/"
    "wd/gkwd_zlhz/202406/t20240603_1133733.html"
)
EXPECTED_MODE_SOURCES = {
    **{
        province: MODE_SOURCE_2019
        for province in (
            "北京",
            "天津",
            "上海",
            "浙江",
            "山东",
            "海南",
            "河北",
            "辽宁",
            "江苏",
            "福建",
            "湖北",
            "湖南",
            "广东",
            "重庆",
        )
    },
    **{
        province: MODE_SOURCE_2021
        for province in ("吉林", "黑龙江", "安徽", "江西", "广西", "贵州", "甘肃")
    },
    **{
        province: MODE_SOURCE_2024
        for province in ("山西", "内蒙古", "河南", "四川", "云南", "陕西", "青海", "宁夏")
    },
}

TOP_LEVEL_FIELDS = {
    "schema_version",
    "verified_at",
    "coverage_note",
    "mode_authority_urls",
    "provinces",
}
PROVINCE_FIELDS = {
    "province",
    "aliases",
    "mode",
    "authority_name",
    "official_roots",
    "mode_source_url",
    "verified_at",
    "notes",
}


def _loads_strict(raw: bytes) -> object:
    text = raw.decode("utf-8", errors="strict")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite(token: str) -> object:
        raise ValueError(f"non-finite JSON number: {token}")

    return json.loads(
        text,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_nonfinite,
    )


def _load_strict(path: Path) -> object:
    return _loads_strict(path.read_bytes())


def _assert_schema(instance: object, schema: dict[str, object], path: str = "$") -> None:
    if "const" in schema and instance != schema["const"]:
        raise AssertionError(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise AssertionError(f"{path}: value is outside enum")

    expected_type = schema.get("type")
    type_matches = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "null": lambda value: value is None,
    }
    if isinstance(expected_type, str) and not type_matches[expected_type](instance):
        raise AssertionError(f"{path}: expected {expected_type}")

    if isinstance(instance, dict):
        required = set(schema.get("required", []))
        missing = required - set(instance)
        if missing:
            raise AssertionError(f"{path}: missing {sorted(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = set(instance) - set(properties)
            if extras:
                raise AssertionError(f"{path}: extra properties {sorted(extras)}")
        for key, value in instance.items():
            if key in properties:
                _assert_schema(value, properties[key], f"{path}.{key}")

    if isinstance(instance, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(instance) < minimum:
            raise AssertionError(f"{path}: too few items")
        if isinstance(maximum, int) and len(instance) > maximum:
            raise AssertionError(f"{path}: too many items")
        if schema.get("uniqueItems") is True:
            fingerprints = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in instance]
            if len(fingerprints) != len(set(fingerprints)):
                raise AssertionError(f"{path}: duplicate items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                _assert_schema(value, item_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(instance) < minimum:
            raise AssertionError(f"{path}: string is too short")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, instance) is None:
            raise AssertionError(f"{path}: string does not match {pattern!r}")


def _parse_public_https(url: str) -> tuple[str, str]:
    split = urlsplit(url)
    if split.scheme != "https":
        raise AssertionError(f"not HTTPS: {url}")
    if split.username is not None or split.password is not None:
        raise AssertionError(f"userinfo is forbidden: {url}")
    if split.query or split.fragment:
        raise AssertionError(f"query/fragment is forbidden: {url}")
    if split.port not in (None, 443):
        raise AssertionError(f"non-default port is forbidden: {url}")
    host = (split.hostname or "").rstrip(".").casefold()
    if not host or "." not in host:
        raise AssertionError(f"host is not public DNS-style: {url}")
    numeric_component = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)", re.IGNORECASE)
    if all(numeric_component.fullmatch(label) for label in host.split(".")):
        raise AssertionError(f"ambiguous numeric-component host is forbidden: {url}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise AssertionError(f"IP literal is forbidden: {url}")
    if host == "localhost" or host.endswith((".local", ".internal", ".test", ".invalid", ".example")):
        raise AssertionError(f"local/reserved host is forbidden: {url}")
    dns_label = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
    if any(dns_label.fullmatch(label) is None for label in host.split(".")):
        raise AssertionError(f"invalid DNS hostname: {url}")
    if not split.path.startswith("/"):
        raise AssertionError(f"non-absolute URL path: {url}")
    return host, split.path


def _canonical_url(url: str) -> str:
    host, path = _parse_public_https(url)
    if host.startswith("www."):
        host = host[4:]
    normalized_path = re.sub(r"/+", "/", path).rstrip("/") or "/"
    return f"{host}{normalized_path}"


def _normalize_alias(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


class StrictJsonParserTest(unittest.TestCase):
    def test_rejects_duplicate_keys(self) -> None:
        with self.assertRaises(ValueError):
            _loads_strict(b'{"province":"A","province":"B"}')

    def test_rejects_nonfinite_numbers(self) -> None:
        for token in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                _loads_strict(b'{"value":' + token + b"}")

    def test_rejects_invalid_utf8(self) -> None:
        with self.assertRaises(UnicodeDecodeError):
            _loads_strict(b'{"value":"\xff"}')


class PublicHttpsUrlOracleTest(unittest.TestCase):
    def test_rejects_ip_literals_and_ambiguous_numeric_host_forms(self) -> None:
        invalid_urls = (
            "https://127.0.0.1/",
            "https://127.1/",
            "https://127.0.1/",
            "https://2130706433/",
            "https://0177.0.0.1/",
            "https://127.000.000.001/",
            "https://1.2.3/",
            "https://0x7f.0.0.1/",
            "https://127.0.0.0x1/",
            "https://0x7f.1/",
            "https://0X7F.0.0.1/",
            "https://127.0.0.0X1/",
            "https://[::1]/",
            "https://[2001:4860:4860::8888]/",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(AssertionError):
                _parse_public_https(url)

    def test_accepts_dns_labels_that_merely_contain_digits(self) -> None:
        for url in ("https://www2.example.com/", "https://exam2026.gov.cn/"):
            with self.subTest(url=url):
                self.assertEqual(_parse_public_https(url)[0], urlsplit(url).hostname)


class ProvinceCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for path, label in (
            (CATALOG_PATH, "province catalog"),
            (SCHEMA_PATH, "province catalog schema"),
            (README_PATH, "province catalog README"),
        ):
            if not path.is_file():
                raise AssertionError(f"{label} is missing")
        cls.catalog = _load_strict(CATALOG_PATH)
        cls.schema = _load_strict(SCHEMA_PATH)

    def test_schema_is_strict_draft_2020_12_and_catalog_matches(self) -> None:
        self.assertEqual(
            self.schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["required"]), TOP_LEVEL_FIELDS)
        province_schema = self.schema["properties"]["provinces"]["items"]
        self.assertFalse(province_schema["additionalProperties"])
        self.assertEqual(set(province_schema["required"]), PROVINCE_FIELDS)
        _assert_schema(self.catalog, self.schema)

    def test_top_level_contract_and_fixed_authorities(self) -> None:
        self.assertEqual(set(self.catalog), TOP_LEVEL_FIELDS)
        self.assertEqual(self.catalog["schema_version"], "1.0")
        self.assertEqual(self.catalog["verified_at"], TASK_DATE.isoformat())
        self.assertEqual(set(self.catalog["mode_authority_urls"]), EXPECTED_MODE_AUTHORITIES)
        self.assertEqual(len(self.catalog["mode_authority_urls"]), 4)
        self.assertEqual(
            {_parse_public_https(url)[0] for url in self.catalog["mode_authority_urls"]},
            {"hudong.moe.gov.cn"},
        )
        self.assertNotIn("www.moe.gov.cn", "\n".join(self.catalog["mode_authority_urls"]))
        coverage = self.catalog["coverage_note"]
        for required_text in ("29", "西藏", "新疆", "未纳入"):
            self.assertIn(required_text, coverage)

    def test_exact_order_set_modes_and_record_fields(self) -> None:
        records = self.catalog["provinces"]
        self.assertEqual(len(records), 29)
        self.assertEqual(tuple(record["province"] for record in records), EXPECTED_ORDER)
        self.assertEqual(
            {record["province"]: record["mode"] for record in records},
            EXPECTED_MODES,
        )
        self.assertEqual(sum(record["mode"] == "3+3" for record in records), 6)
        self.assertEqual(sum(record["mode"] == "3+1+2" for record in records), 23)
        self.assertTrue({"西藏", "新疆"}.isdisjoint(record["province"] for record in records))
        for record in records:
            self.assertEqual(set(record), PROVINCE_FIELDS)

    def test_authority_and_root_oracle_detects_deletion_duplication_or_misassignment(self) -> None:
        actual = {
            record["province"]: (
                record["authority_name"],
                tuple(record["official_roots"]),
            )
            for record in self.catalog["provinces"]
        }
        self.assertEqual(actual, EXPECTED_AUTHORITIES_AND_ROOTS)
        all_roots = [root for _, roots in actual.values() for root in roots]
        self.assertEqual(len(all_roots), len(set(map(_canonical_url, all_roots))))

    def test_aliases_are_nonempty_and_globally_unique_after_nfkc_casefold(self) -> None:
        alias_schema = self.schema["properties"]["provinces"]["items"]["properties"]["aliases"]
        self.assertEqual(alias_schema["maxItems"], MAX_ALIASES_PER_PROVINCE)
        aliases: dict[str, str] = {}
        for record in self.catalog["provinces"]:
            self.assertTrue(record["aliases"])
            self.assertLessEqual(len(record["aliases"]), MAX_ALIASES_PER_PROVINCE)
            normalized = [_normalize_alias(alias) for alias in record["aliases"]]
            self.assertEqual(len(normalized), len(set(normalized)))
            self.assertIn(_normalize_alias(record["province"]), normalized)
            for alias in normalized:
                self.assertNotIn(alias, aliases, f"alias shared by {aliases.get(alias)} and {record['province']}")
                aliases[alias] = record["province"]
        self.assertLessEqual(
            len(aliases),
            len(EXPECTED_ORDER) * MAX_ALIASES_PER_PROVINCE,
        )

        over_limit = json.loads(json.dumps(self.catalog, ensure_ascii=False))
        over_limit["provinces"][0]["aliases"].extend(["京城", "首都地区"])
        with self.assertRaises(AssertionError):
            _assert_schema(over_limit, self.schema)

    def test_roots_and_evidence_are_public_https_urls(self) -> None:
        all_urls = list(self.catalog["mode_authority_urls"])
        for record in self.catalog["provinces"]:
            self.assertGreaterEqual(len(record["official_roots"]), 1)
            all_urls.extend(record["official_roots"])
            all_urls.append(record["mode_source_url"])
            source_host, _ = _parse_public_https(record["mode_source_url"])
            self.assertEqual(source_host, "hudong.moe.gov.cn")
            self.assertEqual(
                record["mode_source_url"],
                EXPECTED_MODE_SOURCES[record["province"]],
            )
        for url in all_urls:
            _parse_public_https(url)

    def test_dates_are_real_and_not_after_task_date(self) -> None:
        dates = [self.catalog["verified_at"]]
        dates.extend(record["verified_at"] for record in self.catalog["provinces"])
        for value in dates:
            parsed = date.fromisoformat(value)
            self.assertLessEqual(parsed, TASK_DATE)
        for record in self.catalog["provinces"]:
            self.assertEqual(record["verified_at"], TASK_DATE.isoformat())

    def test_jiangxi_uses_a_discovery_root_and_explains_excluded_hosts(self) -> None:
        record = next(record for record in self.catalog["provinces"] if record["province"] == "江西")
        self.assertEqual(record["official_roots"], ["https://jyt.jiangxi.gov.cn/"])
        for marker in ("www.jxeea.cn", "仅HTTP", "未纳入", "jxgk", "业务系统"):
            self.assertIn(marker, record["notes"])

    def test_tianjin_starts_discovery_at_the_education_authority(self) -> None:
        record = next(record for record in self.catalog["provinces"] if record["province"] == "天津")
        self.assertEqual(record["official_roots"], ["https://jy.tj.gov.cn/"])
        for marker in ("zhaokao", "HTTPS", "未证实", "市教委", "发现"):
            self.assertIn(marker, record["notes"])

    def test_shanxi_excludes_the_service_and_http_only_exam_hosts(self) -> None:
        record = next(record for record in self.catalog["provinces"] if record["province"] == "山西")
        self.assertEqual(record["official_roots"], ["https://jyt.shanxi.gov.cn/"])
        for marker in ("sxkszx", "仅HTTP", "gkpt", "服务页", "省教育厅", "高考"):
            self.assertIn(marker, record["notes"])

    def test_catalog_has_no_volatile_sensitive_local_or_third_party_payload(self) -> None:
        serialized = json.dumps(self.catalog, ensure_ascii=False, sort_keys=True).casefold()
        forbidden_terms = (
            "score",
            "rank",
            "cutoff",
            "admission_line",
            "school_recommend",
            "price",
            "student_name",
            "phone_number",
            "id_card",
            "分数",
            "位次",
            "排名",
            "录取线",
            "投档线",
            "学校推荐",
            "价格",
            "身份证",
            "手机号",
            "自媒体",
            "第三方",
            "file://",
            "/home/",
            "/users/",
        )
        for term in forbidden_terms:
            self.assertNotIn(term, serialized)
        self.assertIsNone(re.search(r"[a-z]:\\\\", serialized))

    def test_readme_documents_maintenance_contract(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        for required_text in (
            "稳定顺序",
            "一手官方来源",
            "重定向",
            "域名变更",
            "模式变更",
            "年度复核",
            "动态数据",
            "西藏",
            "新疆",
            "官方实施",
            "江西",
            "HTTP",
        ):
            self.assertIn(required_text, readme)

    def test_readme_explains_safe_discovery_fallbacks(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        for required_text in (
            "www.zhaokao.net",
            "www.sxkszx.cn",
            "www.jxeea.cn",
            "gkpt",
            "jxgk",
            "发现根",
            "仅提供 HTTP",
            "machine catalog 不包含",
        ):
            self.assertIn(required_text, readme)


if __name__ == "__main__":
    unittest.main()
