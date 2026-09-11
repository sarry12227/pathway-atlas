"""Fast, source-linked planning references with a fixed family-facing layout.

This pure module does not certify a research receipt or predict admission.
The host supplies read public text; school-rank interpolation and selection are
deterministic, and the original evidence journal remains independent.
"""
from __future__ import annotations

from bisect import bisect_left
import math

from scripts.adapters.public_text import PublicTextAdapterError, PublicTextField, bind_public_text
from scripts.path_recommend import validate_public_output_text
from scripts.planning_profile import PlanningProfile


POLICY_VERSION = "planning-reference-v1"
TIERS = ("冲", "稳", "保")
ORDINARY_COUNTS = dict(zip(TIERS, (3, 4, 5)))
PATHWAY_NAMES = {
    "strong_foundation": "强基计划",
    "comprehensive_evaluation": "综合评价",
    "hong_kong_macao": "港澳升学",
}
INTRODUCTIONS = {
    "strong_foundation": (
        "是什么：面向基础学科等领域选拔培养人才的招生途径，适合对相关学科有持续兴趣的学生。",
        "培养模式：通常实行专门培养方案，重视基础研究及进一步深造；本研衔接、考核与转段按各校方案执行。",
    ),
    "comprehensive_evaluation": (
        "是什么：结合高考及其他考核信息进行选拔的招生方式；浙江三位一体属于相关选择，具体评价项目和权重因校而异。",
        "培养模式：录取后按所报专业或专项方案培养；综合评价主要是选拔方式，本身不等于保研或特殊学位。",
    ),
    "hong_kong_macao": (
        "是什么：申请香港、澳门高校的本科招生，可涉及内地统招或独立申请，须按具体院校区分。",
        "培养模式：按当地高校的本科课程和学位制度培养，授课语言、学制、通识与专业安排以项目说明为准。",
    ),
}
DISCLAIMER = "AI生成，仅供升学规划参考；不构成录取承诺，正式报考以当年官方要求为准。"


def _text(value, name, *, limit=400):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} requires concise nonempty text")
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be one line")
    validate_public_output_text(value)
    return value


def _number(value, name, *, minimum=1, maximum=10000000):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside its numeric range")
    return value


def _year(value, research_year):
    if type(value) is not int or not research_year - 3 <= value <= research_year:
        raise ValueError("source year must be in the current four-year reference window")
    return value


class _Sources:
    def __init__(self, records, research_year):
        self.records = {}
        self.used = set()
        for raw in records:
            key = _text(raw["source_id"], "source_id", limit=100)
            if key in self.records:
                raise ValueError("duplicate source_id")
            _year(raw["year"], research_year)
            _text(raw["title"], "source title", limit=150)
            # PublicTextAdapter validates source IDs, public URLs and privacy.
            bind_public_text(source_id=key, url=raw["url"], text=raw["text"],
                             fields={"missing": PublicTextField.missing()})
            self.records[key] = dict(raw)

    def cite(self, record, values=(), *, year=None):
        citation = record["citation"]
        source = self.records.get(citation["source_id"])
        if source is None or (year is not None and source["year"] != year):
            raise ValueError("citation source or year does not match")
        bound = PublicTextField(value=list(values), quote=citation["quote"],
                               start=citation.get("start"), end=citation.get("end"))
        try:
            bind_public_text(source_id=source["source_id"], url=source["url"],
                             text=source["text"], fields={"claim": bound})
        except PublicTextAdapterError as error:
            raise ValueError("brief claim is not grounded in the saved public quote") from error
        self.used.add(source["source_id"])
        return f"[{source['year']}年材料]({source['url']})"

    def field(self, record, field, value, *, year):
        citation = record.get("citations", {}).get(field, record.get("citation"))
        if citation is None:
            raise ValueError(f"{field} requires a public citation")
        return self.cite({"citation": citation}, (value,), year=year)


def _interpolate(x, points):
    points = sorted(points)
    if len(points) < 2 or len({p[0] for p in points}) != len(points):
        raise ValueError("calibration requires at least two distinct rank anchors")
    if any(b[1] <= a[1] for a, b in zip(points, points[1:])):
        raise ValueError("rank anchors must be strictly monotone")
    # Do not silently extrapolate an unsupported part of the school cohort.
    if not points[0][0] <= x <= points[-1][0]:
        return None
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return round(y0 + (x - x0) * (y1 - y0) / (x1 - x0))
    return None


def _score_rows(profile, table, sources, research_year):
    year = _year(table["year"], research_year)
    expected_group = profile.subject_group if profile.subject_mode == "3+1+2" else "综合"
    if table.get("subject_group") != expected_group:
        raise ValueError("score table subject group does not match the province examination system")
    rows, links = [], []
    for row in table["rows"]:
        score = _number(row["score"], "gaokao score", minimum=0, maximum=900)
        rank = _number(row["rank"], "gaokao rank")
        links.append(sources.cite(row, (score, rank), year=year))
        rows.append((rank, score))
    rows.sort()
    if len(rows) < 2 or len({r for r, _ in rows}) != len(rows) or any(b[1] >= a[1] for a, b in zip(rows, rows[1:])):
        raise ValueError("score table must have distinct increasing ranks and decreasing scores")
    return year, rows, links


def _position(profile, payload, sources, research_year):
    observations = sorted(profile.rank_observations, key=lambda obs: obs.exam_date)
    latest = observations[-1] if observations else None
    result = {"exam_score": latest.score if latest else None,
              "exam_max_score": latest.max_score if latest else None,
              "central_rank": None, "rank_bounds": None,
              "strategy_bounds": None,
              "score": None, "score_bounds": None, "reference_year": None,
              "method": "unavailable", "basis": "暂无可比校准资料", "citations": []}
    model = payload.get("calibration", {"method": "unavailable", "basis": "暂无可比校准资料"})
    method = model["method"]
    if method not in {"school_rank", "official_rank", "official_score", "unavailable"}:
        raise ValueError("unsupported calibration method")
    result["basis"] = _text(model["basis"], "calibration basis")
    if method == "unavailable":
        return result
    if latest is None or (latest.rank is None and method != "official_score"):
        result["basis"] = "已有学校资料，但本次校排尚不明确；先给典型目标梯度"
        return result
    if method in {"official_rank", "official_score"}:
        if latest.scope != "province_official" or profile.exam_year != research_year:
            raise ValueError("official_rank requires the actual current gaokao rank")
        if method == "official_score":
            table = payload.get("score_table")
            if table is None or table["year"] != research_year:
                raise ValueError("official score mapping requires the matching current-year table")
            _, rows, _ = _score_rows(profile, table, sources, research_year)
            matches = [rank for rank, score in rows if score == latest.score]
            if len(matches) != 1:
                raise ValueError("the actual gaokao score has no exact row in the score table")
            center = matches[0]
        else:
            center = latest.rank
        bounds = [center, center]
        basis = "用户确认的当年正式高考位次" if method == "official_rank" else "用户确认的当年高考分数对应同分累计位次参考"
    else:
        if latest.scope != "school":
            raise ValueError("school_rank requires a school-scope rank")
        comparability = model["comparability"]
        if comparability == "same_school":
            if model["school"] != profile.high_school:
                raise ValueError("calibration school does not match profile")
        elif comparability == "regional_comparable":
            _text(model["comparability_reason"], "regional comparability reason")
        else:
            raise ValueError("calibration must identify the comparable school cohort")
        year = _year(model["year"], research_year)
        points = []
        for anchor in model["anchors"]:
            x = _number(anchor["school_rank"], "school rank")
            y = _number(anchor["province_rank"], "province rank")
            result["citations"].append(sources.cite(anchor, (x, y), year=year))
            points.append((x, y))
        center = _interpolate(latest.rank, points)
        if center is None:
            result["basis"] = "本次校排超出已读取锚点范围；先按真实学校资料给目标梯度"
            return result
        margin = _number(model.get("margin_fraction", 0.2), "planning margin", minimum=0.1, maximum=0.5)
        bounds = [max(1, math.floor(center * (1 - margin))), math.ceil(center * (1 + margin))]
        for school_rank in (profile.best_rank, profile.usual_rank):
            if school_rank is not None:
                rank = _interpolate(school_rank, points)
                if rank is not None:
                    bounds = [min(bounds[0], rank), max(bounds[1], rank)]
        basis = (f"{year}年学校出口锚点按校排插值；{result['basis']}；"
                 f"上下浮动{margin:.0%}为规划敏感性假设，非统计置信区间，不假定未来成绩自动提高")
    strategy_bounds = bounds if method == "school_rank" else [max(1, math.floor(center * 0.8)), math.ceil(center * 1.2)]
    result.update(central_rank=center, rank_bounds=bounds, strategy_bounds=strategy_bounds, method=method, basis=basis)
    table = payload.get("score_table")
    if table:
        year, rows, links = _score_rows(profile, table, sources, research_year)
        result["citations"] += links
        def score_at(rank):
            if not rows[0][0] <= rank <= rows[-1][0]:
                return None
            index = bisect_left([r for r, _ in rows], rank)
            return rows[index][1]
        result.update(score=score_at(center), reference_year=year)
        lo, hi = score_at(bounds[1]), score_at(bounds[0])
        if lo is not None and hi is not None:
            result["score_bounds"] = [lo, hi]
    result["citations"] = list(dict.fromkeys(result["citations"]))
    return result


def _candidates(profile, payload, sources, positioning, research_year):
    groups = {kind: {tier: [] for tier in TIERS} for kind in ("ordinary", *PATHWAY_NAMES)}
    selected_subjects = set(profile.subject_group.split("+")) | set(profile.secondary_subjects)
    center = positioning["central_rank"]
    for raw in payload.get("candidates", []):
        kind = raw["kind"]
        if kind not in groups:
            raise ValueError("unsupported candidate pathway")
        if raw["province"] != profile.province:
            raise ValueError("candidate admissions province differs from the profile")
        year = _year(raw["year"], research_year)
        if raw["fit"] not in {"matched", "conditional", "blocked"}:
            raise ValueError("candidate fit must be explicit")
        if raw["fit"] == "blocked" or (kind == "ordinary" and raw["location_province"] != profile.province):
            continue
        preferences = raw["matches_preferences"]
        if not isinstance(preferences, list) or not set(preferences) <= set(profile.target_majors):
            raise ValueError("major preference matches must refer to the confirmed profile")
        if profile.target_majors and not preferences:
            continue
        required = raw.get("required_subjects")
        if required is not None and (not isinstance(required, list) or not all(isinstance(s, str) for s in required)):
            raise ValueError("required_subjects is an AND list, or null when unverified")
        if required is not None and not set(required) <= selected_subjects:
            continue
        fields = ("school", "major", "fit_reason", "cautions")
        item = {key: _text(raw[key], key) for key in fields}
        threshold = raw.get("threshold_rank")
        threshold_basis = raw.get("threshold_basis")
        if threshold_basis not in {"admission", "shortlist", "planning_benchmark", "unavailable"}:
            raise ValueError("candidate threshold basis must be explicit")
        links = [sources.field(raw, field, item[field], year=year) for field in ("school", "major")]
        for field in ("cultivation", "selection"):
            if raw.get(field):
                item[field] = _text(raw[field], field)
                links.append(sources.field(raw, field, item[field], year=year))
            else:
                item[field] = "校级细则待核验，先按该路径的一般培养方式理解"
        if required is not None:
            links.append(sources.field(raw, "required_subjects", required, year=year))
        if threshold is not None:
            _number(threshold, "threshold rank")
            links.append(sources.field(raw, "threshold_rank", threshold, year=year))
        if threshold is not None and threshold_basis == "unavailable":
            raise ValueError("a threshold number requires its real basis")
        citation = " ".join(dict.fromkeys(links))
        if center is not None and threshold is not None:
            lower, upper = positioning["strategy_bounds"]
            if threshold < lower:
                continue
            tier = "冲" if threshold < center else "稳" if threshold < upper else "保"
        else:
            tier = raw.get("benchmark_tier")
            if tier not in TIERS:
                raise ValueError("unpositioned examples require a target tier")
        item.update(threshold_rank=threshold, threshold_basis=threshold_basis,
                    fit="conditional" if required is None else raw["fit"],
                    year=year, citation=citation, matches_preferences=list(preferences),
                    required_subjects=required, personal_tier=center is not None and threshold is not None)
        groups[kind][tier].append(item)
    for kind, tiers in groups.items():
        seen = set()
        for tier, items in tiers.items():
            items.sort(key=lambda item: (abs((item["threshold_rank"] or 0) - (center or 0)), item["school"], item["major"]))
            chosen = []
            for item in items:
                if item["school"] not in seen:
                    seen.add(item["school"])
                    chosen.append(item)
                limit = ORDINARY_COUNTS[tier] if kind == "ordinary" else 1
                if len(chosen) == limit:
                    break
            tiers[tier] = chosen
    return groups


def _render_groups(groups, *, counts, personal_rank, pathway=False):
    lines = [""]
    all_items = [item for items in groups.values() for item in items]
    repeated_caution = {item["cautions"] for item in all_items}
    common_caution = next(iter(repeated_caution)) if len(all_items) > 1 and len(repeated_caution) == 1 else None
    if common_caution:
        lines += [f"共同需留意：{common_caution}。", ""]
    for tier in TIERS:
        items = groups[tier]
        count = counts[tier]
        lines.append(f"**{tier}{count}所**")
        lines.append("")
        for index, item in enumerate(items, 1):
            basis = "历史专业录取参考" if item["threshold_basis"] == "admission" else "历史入围参考"
            if item["threshold_basis"] == "planning_benchmark":
                basis = "普通批参照，不能代表本路径入围线" if pathway else "目标参照"
            threshold = f"{basis}约{item['threshold_rank']}位" if item["threshold_rank"] is not None else "录取/入围门槛尚待补齐"
            conditional = "；条件待核实" if item["fit"] == "conditional" else ""
            if not personal_rank or not item["personal_tier"]:
                conditional += "；目标梯度，未判断个人录取把握"
            lines.append(f"{index}. **{item['school']}｜{item['major']}**：{item['fit_reason']}。{threshold}{conditional}。{item['citation']}。")
            if pathway:
                lines.append(f"   培养：{item['cultivation']}；选拔：{item['selection']}。")
            if not common_caution:
                lines.append(f"   需留意：{item['cautions']}。")
        if len(items) < count:
            lines.append(f"本档已有{len(items)}所，缺{count - len(items)}所符合当前地域、专业和已知条件的候选；先使用已列目标，扩大地域或新增公开材料后补足。")
        lines.append("")
    return lines


def build_planning_brief(profile: PlanningProfile, payload: dict, *, research_year: int) -> dict:
    """Produce a fixed planning reference from a confirmed profile and read sources."""
    if type(profile) is not PlanningProfile or payload.get("schema_version") != "1.0":
        raise ValueError("planning brief requires the supported profile and submission schema")
    if payload.get("profile_digest") != profile.digest:
        raise ValueError("brief submission belongs to a different confirmed profile")
    if (payload.get("province"), payload.get("subject_mode"), payload.get("research_year")) != (profile.province, profile.subject_mode, research_year):
        raise ValueError("brief province, subject mode or research year differs from the session")
    if set(payload.get("selection", [])) != {profile.subject_group, *profile.secondary_subjects}:
        raise ValueError("brief subject selection differs from the confirmed profile")
    sources = _Sources(payload.get("sources", []), research_year)
    positioning = _position(profile, payload, sources, research_year)
    groups = _candidates(profile, payload, sources, positioning, research_year)
    score, maximum = positioning["exam_score"], positioning["exam_max_score"]
    lines = ["# 多元星途｜本次升学参考规划", "", "## 一、成绩定位", "",
             f"本次考试：{score if score is not None else '未提供'}/{maximum if maximum is not None else '未知满分'}分。"]
    center = positioning["central_rank"]
    if center is not None:
        lo, hi = positioning["rank_bounds"]
        if positioning["score"] is not None:
            year = positioning["reference_year"]
            lines.append(f"按{year}年已公布高考口径作规划参考：**对应高考大致约{positioning['score']}分，折合{profile.province}省排约{center}位**。")
            if positioning["score_bounds"]:
                lines.append(f"分数参考范围：{positioning['score_bounds'][0]}–{positioning['score_bounds'][1]}分；省排参考范围：{lo}–{hi}位。")
        else:
            lines.append(f"**折合省排中心参考约{center}位，范围{lo}–{hi}位**；对应分数待同口径一分一段表补齐，院校与路径先按位次推进。")
        lines.append(f"估算依据：{positioning['basis']}。" + " ".join(positioning["citations"]))
        if profile.exam_year != research_year:
            lines.append(f"这是现阶段用于选校和安排准备的历史口径定位，不是对{profile.exam_year}年实际高考结果的保证。")
    else:
        lines.append(f"个人高考分数和省排暂缺可比校准：{positioning['basis']}。下面先给有来源的典型学校目标梯度和路径；这些档位不是已测得的个人冲稳保，不把校内裸分直接当高考分。")
    lines += ["", "## 二、本省普通批：冲3所、稳4所、保5所", "",
              "优先匹配已选专业与本省地域；以下档位用于规划，保档也不代表保证录取。"]
    if positioning["method"] in {"official_rank", "official_score"}:
        lines.append("已知高考位次不再估算；分档采用位次上下20%的规划窗口，不表示本人位次有20%误差。")
    lines += _render_groups(groups["ordinary"], counts=ORDINARY_COUNTS, personal_rank=center is not None)
    for numeral, (kind, title) in zip(("三", "四", "五"), PATHWAY_NAMES.items()):
        lines += [f"## {numeral}、{title}", "", *INTRODUCTIONS[kind], ""]
        decision = payload.get("pathway_decisions", {}).get(kind, "先保留为准备方向，具体资格和投入顺序按已读项目判断")
        lines += ["本次判断：" + _text(decision, "pathway decision") + "。", ""]
        lines += _render_groups(groups[kind], counts=dict.fromkeys(TIERS, 1), personal_rank=center is not None, pathway=True)
    lines += ["## 六、其他路径", ""]
    others = payload.get("other_pathways", [])
    if not others:
        lines.append("专项计划、公费师范/定向培养、军警、艺体和中外合作按画像分别筛选；当前尚未取得支持新增可行项目的材料，先集中准备上面已列方向。")
    for item in others:
        name = _text(item["name"], "other pathway")
        description = _text(item["description"], "other pathway description")
        link = sources.cite(item, (name, description), year=_year(item["year"], research_year))
        lines.append(f"- **{name}**：{description}。本次建议：{_text(item['fit_reason'], 'pathway fit')}。{link}")
    lines += ["", "## 七、现阶段行动", ""]
    actions = payload.get("actions", [])
    if not 1 <= len(actions) <= 5:
        raise ValueError("brief requires one to five concrete current actions")
    lines += [f"{i}. {_text(action, 'action')}。" for i, action in enumerate(actions, 1)]
    lines += ["", DISCLAIMER]
    return {"policy_version": POLICY_VERSION, "profile_digest": profile.digest,
            "positioning": positioning, "ordinary": groups["ordinary"],
            "pathways": {kind: groups[kind] for kind in PATHWAY_NAMES},
            "report_text": "\n".join(lines),
            "sources": [{key: source[key] for key in ("source_id", "url", "title", "year")}
                        for sid, source in sources.records.items() if sid in sources.used],
            "delivery": {"mode": "planning_reference", "authenticated_research": False,
                         "personal_positioning": center is not None}}
