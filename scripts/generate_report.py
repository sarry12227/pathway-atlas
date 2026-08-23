# -*- coding: utf-8 -*-
"""CLI 入口：一次调用完成全管线（M3 可选 → M4 → M5）并产出 Markdown
升学方案报告（spec §4.4 四部分结构 / §4.5 可追溯 / AC7 合规扫描）。

内部直接 import 三个引擎模块（不起子进程）；stdout 输出 JSON 摘要
（报告路径、各章节行数、数据来源清单），错误走 stderr + 退出码 2。

报告生成后全文经 compliance_scan 价格/营销词扫描，命中即删除报告文件、
退出码 2、stderr 报告命中片段——拒绝交付（AC7 Markdown 侧）。

用法：
    python scripts/generate_report.py --province 湖北 --subject-group 物理 \
        --grade 高三 (--score 620 | --rank 15000 | \
        --school 武汉XXX中学 --exam-rank 120 [--best-rank 80 --normal-rank 150]) \
        [--intent-schools 武汉大学] [--major-category 计算机] [--city 武汉] \
        [--hkmo-willingness 考虑|不考虑|可了解] [--has-awards] [--has-activities] \
        [--name 张三] [--output output/张三_升学方案_20260821.md]
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compliance_scan import find_price_text  # noqa: E402
from data_loader import (DEFAULT_DATA_ROOT, DataError, load_path_table,  # noqa: E402
                         load_province_config, load_toudang, load_yifenyiduan,
                         score_to_rank)
from path_recommend import (HKMO_POSITIVE, PathRecommendError,  # noqa: E402
                            equiv_adjust_from_config, recommend_paths)
from rank_calc import RankCalcError, estimate_rank  # noqa: E402
from school_recommend import (SchoolRecommendError, params_from_config,  # noqa: E402
                              recommend_schools)

# Evidence-aware v0.1 public path.  The historical imports above remain only
# for the one-release CLI adapter exercised by existing users and tests.
from contracts import EvidenceStatus, RecommendationProfile  # noqa: E402
from path_recommend import PathwayProfile, evaluate_pathways  # noqa: E402
from report_model import (StudentProfile, build_report_model, render_markdown,  # noqa: E402
                          validate_profile_text)
from validate_data import (ValidatedAdmissionRow,  # noqa: E402
                           validate_dataset_snapshot)
from validate_evidence import validate_bundle_snapshot  # noqa: E402

DISCLAIMER = ("本方案基于历史公开数据生成，仅为数据参考，不构成录取承诺；"
              "志愿填报与路径申报以省教育考试院及各高校官方发布的当年信息为准。")

# 数据文件用途说明（数据来源清单用）
USAGE = {
    "yifenyiduan": "一分一段：分数↔省排名反查、估分对应分数换算",
    "tou_dang": "普通批投档线：冲稳保推荐",
    "xibao": "喜报锚点：校排名→省排名折算",
    "schools": "高中名录：折算降级链「同城同档代理」定位",
    "qiangji": "强基计划路径推荐",
    "zongping": "综合评价路径推荐",
    "gangao": "港澳院校路径推荐",
}

# 估分数据质量等级标注说明（原样展示引擎 data_quality 并附读法）
QUALITY_NOTE = {
    "full": "full（完整）",
    "partial": "partial（部分：往年回退，较当年数据降一级标注）",
    "sparse": "sparse（稀疏：代理或二次回退数据）",
}

EMPTY_TIER_NOTE = "> 本档无符合条件院校：按规则留空，不硬凑。"


class EvidenceReportInputError(ValueError):
    """The public report CLI received invalid or unauthenticated input."""


class EvidenceReportCapabilityError(RuntimeError):
    """A caller-required optional report capability is unavailable."""


def _reconfigure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="升学方案报告生成（M3 可选 → M4 → M5 全管线，Markdown 四部分）")
    p.add_argument("--province", required=True, help="省份，如：湖北")
    p.add_argument("--subject-group", required=True, help="科目组：物理/历史")
    p.add_argument("--grade", required=True,
                   choices=["高一", "高二", "高三"], help="年级")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--score", type=int, help="高考分数（经一分一段反查省排名）")
    group.add_argument("--rank", type=int, help="省排名（直接输入，跳过反查与折算）")
    group.add_argument("--school", help="高中校名（估分路径，需配合 --exam-rank）")
    p.add_argument("--exam-rank", type=int, default=None,
                   help="最近一次的校排名（估分路径必填）")
    p.add_argument("--best-rank", type=int, default=None, help="最好一次的校排名")
    p.add_argument("--normal-rank", type=int, default=None, help="正常水平的校排名")
    p.add_argument("--intent-schools", nargs="*", default=None,
                   help="意向院校（选填，全称）")
    p.add_argument("--major-category", nargs="*", default=None,
                   help="专业倾向（选填，如：计算机 临床医学）")
    p.add_argument("--city", nargs="*", default=None,
                   help="目标城市（选填，仅透传，不参与排序）")
    p.add_argument("--hkmo-willingness", default=None,
                   choices=["考虑", "不考虑", "可了解"],
                   help="港澳意愿（选填；不考虑或未填则不推荐港澳路径）")
    p.add_argument("--secondary-subjects", nargs="*", default=None,
                   help="再选科目（选填，如：化学 生物；作为推荐解读上下文）")
    p.add_argument("--has-awards", action="store_true", help="有学科竞赛获奖经历")
    p.add_argument("--has-activities", action="store_true",
                   help="有科创/社会实践等活动经历")
    p.add_argument("--name", default=None,
                   help="学生姓名（选填，仅用于报告标题与文件名，不写入其他文件）")
    p.add_argument("--output", default=None,
                   help="报告输出路径（默认 output/{姓名或\"方案\"}_升学方案_{日期}.md）")
    p.add_argument("--data-root", default=DEFAULT_DATA_ROOT,
                   help=argparse.SUPPRESS)  # 测试/贡献者调试用
    return p


# ---------------------------------------------------------------- 数据管线

def _csv_inventory(province_dir: str, table: str) -> dict:
    """读取数据文件行数与年份集合（数据来源清单用，只读不改）。"""
    path = os.path.join(province_dir, f"{table}.csv")
    years = set()
    rows = 0
    with open(path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            rows += 1
            if raw.get("year"):
                years.add(int(raw["year"]))
    return {"file": f"{table}.csv", "years": sorted(years), "rows": rows,
            "usage": USAGE.get(table, "")}


def collect_context(args) -> dict:
    """全管线计算：成绩定位（三选一）→ M4 → M5，返回渲染上下文。

    所有院校数据均来自引擎返回的 dict，本函数不做任何院校信息二次加工。
    """
    ctx = {
        "province": args.province, "subject_group": args.subject_group,
        "grade": args.grade, "name": args.name,
        "intent_schools": args.intent_schools or [],
        "major_category": args.major_category or [],
        "city": args.city or [],
        "hkmo_willingness": args.hkmo_willingness,
        "secondary_subjects": getattr(args, "secondary_subjects", None) or [],
        "has_awards": args.has_awards, "has_activities": args.has_activities,
        "date": date.today().isoformat(),
        "m3": None, "score_lookup": None,
    }
    tables_used = []

    # Step 1：成绩定位（三种输入方式互斥）
    if args.rank is not None:
        ctx["method"] = "rank"
        ctx["rank"] = args.rank
        ref_rank = args.rank
    elif args.score is not None:
        ctx["method"] = "score"
        ctx["score"] = args.score
        lookup = score_to_rank(args.province, args.subject_group, args.score,
                               root=args.data_root)
        # 精确命中 / 相邻低分缺档的如实标注（只读判断，不改反查口径）
        _, yfd_rows = load_yifenyiduan(args.province, args.subject_group,
                                       root=args.data_root)
        lookup["exact"] = any(r["score"] == args.score for r in yfd_rows)
        ctx["score_lookup"] = lookup
        ref_rank = lookup["rank"]
        tables_used.append("yifenyiduan")
    else:
        if args.exam_rank is None:
            raise DataError("估分路径缺少 --exam-rank（最近一次校排名）")
        ctx["method"] = "school"
        ctx["school"] = args.school
        ctx["exam_rank"] = args.exam_rank
        ctx["best_rank"] = args.best_rank
        ctx["normal_rank"] = args.normal_rank
        m3 = estimate_rank(args.province, args.school, args.subject_group,
                           exam_rank=args.exam_rank, best_rank=args.best_rank,
                           normal_rank=args.normal_rank, root=args.data_root)
        ctx["m3"] = m3
        ref_rank = m3["normal_estimate"]["prov_rank"]
        tables_used.extend(["xibao", "schools", "yifenyiduan"])
    ctx["ref_rank"] = ref_rank

    # Step 2：M4 普通批冲稳保
    config = load_province_config(args.province, root=args.data_root,
                                  require_anchors=False)
    year, rows = load_toudang(args.province, args.subject_group,
                              root=args.data_root)
    ctx["m4"] = recommend_schools(
        rows, year=year, estimated_prov_rank=ref_rank,
        subject_group=args.subject_group,
        target_major_category=args.major_category,
        target_city=args.city,
        target_schools_preference=args.intent_schools,
        secondary_subjects=ctx.get("secondary_subjects") or None,
        params=params_from_config(config))
    tables_used.append("tou_dang")

    # Step 3：M5 多元路径（港澳意愿非"考虑/可了解"时不加载港澳数据）
    qiangji_rows = load_path_table(args.province, "qiangji", root=args.data_root)
    zongping_rows = load_path_table(args.province, "zongping", root=args.data_root)
    gangao_rows = None
    tables_used.extend(["qiangji", "zongping"])
    if args.hkmo_willingness in HKMO_POSITIVE:
        gangao_rows = load_path_table(args.province, "gangao", root=args.data_root)
        tables_used.append("gangao")
    ctx["m5"] = recommend_paths(
        qiangji_rows, zongping_rows, gangao_rows,
        estimated_prov_rank=ref_rank, grade=args.grade,
        hkmo_willingness=args.hkmo_willingness,
        has_awards=args.has_awards, has_activities=args.has_activities,
        params=params_from_config(config),
        equiv_rank_adjust=equiv_adjust_from_config(config))

    ctx["data_sources"] = [_csv_inventory(_province_dir(args), t)
                           for t in tables_used]
    return ctx


def _province_dir(args) -> str:
    """通过省份元数据解析数据清单目录。"""
    from data_loader import get_province_dir
    return os.fspath(get_province_dir(args.province, args.data_root))


# ---------------------------------------------------------------- 报告渲染

def _num(v) -> str:
    """数值展示：浮点去掉多余的 .0。"""
    if isinstance(v, float):
        return f"{v:g}"
    return "—" if v is None else str(v)


def _delta(d) -> str:
    return f"{d:+d}" if isinstance(d, int) else "—"


def _table(headers, rows) -> list:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "---|" * len(headers)]
    lines.extend("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
    return lines


def _input_summary(ctx) -> list:
    if ctx["method"] == "score":
        score_text = f"分数 {ctx['score']} 分"
    elif ctx["method"] == "rank":
        score_text = f"省排名 {ctx['rank']} 位"
    else:
        parts = [f"校排名 {ctx['exam_rank']}"]
        if ctx.get("best_rank"):
            parts.append(f"最好 {ctx['best_rank']}")
        if ctx.get("normal_rank"):
            parts.append(f"正常 {ctx['normal_rank']}")
        score_text = f"校排名折算：{ctx['school']}（{'，'.join(parts)}）"
    willingness = ctx["hkmo_willingness"] or "未填"
    return _table(
        ["项目", "内容"],
        [["省份 / 科目组", f"{ctx['province']} · {ctx['subject_group']}组"],
         ["再选科目", "、".join(ctx.get("secondary_subjects") or []) or "未填"],
         ["年级", ctx["grade"]],
         ["成绩输入", score_text],
         ["意向院校", "、".join(ctx["intent_schools"]) or "未填"],
         ["专业倾向", "、".join(ctx["major_category"]) or "未填"],
         ["目标城市", "、".join(ctx["city"]) or "未填"],
         ["港澳意愿", willingness],
         ["获奖经历", "有" if ctx["has_awards"] else "无"],
         ["活动经历", "有" if ctx["has_activities"] else "无"]])


def _positioning(ctx) -> list:
    """1.2 成绩定位：按输入方式如实渲染；未走的路径明确说明。"""
    lines = ["### 1.2 成绩定位", ""]
    if ctx["method"] == "rank":
        lines += ["**定位方式：直接输入省排名**", "",
                  f"- 省排名 {ctx['rank']} 位（用户直接输入）。",
                  "> 本次未经过一分一段反查与校排名折算（M3），无折算容差；"
                  "定位精度取决于输入位次本身。", ""]
    elif ctx["method"] == "score":
        lk = ctx["score_lookup"]
        if lk["exact"]:
            hit = f"精确命中 {lk['year']} 年一分一段表"
        else:
            hit = (f"{lk['year']} 年一分一段表无该分整档记录，"
                   f"按相邻低分档反查")
        lines += ["**定位方式：分数 → 省排名（一分一段反查）**", "",
                  f"- 输入分数 {lk['score']} 分，{hit}，"
                  f"对应省排名 {lk['rank']} 位。",
                  "- 反查口径：精确命中取该分累计人数；缺档取相邻低分累计。",
                  "> 本次未使用校排名折算（M3 估分）：成绩由分数直接反查，"
                  "无折算容差。", ""]
    else:
        m3 = ctx["m3"]
        info = m3["anchor_info"]
        quality = QUALITY_NOTE.get(info["data_quality"], info["data_quality"])
        lines += ["**定位方式：校排名 → 省排名（M3 锚点折算）**", ""]
        lines += _table(
            ["估计口径", "省排名（约）", "对应分数（约）"],
            [["正常水平", m3["normal_estimate"]["prov_rank"],
              m3["normal_estimate"]["estimated_score"]],
             ["最好水平", m3["best_estimate"]["prov_rank"],
              m3["best_estimate"]["estimated_score"]],
             ["保守估计", m3["conservative_estimate"]["prov_rank"],
              m3["conservative_estimate"]["estimated_score"]]])
        lines += ["",
                  f"- 排名区间：{m3['interval']['rank_range']} 位；"
                  f"分数区间：{m3['interval']['score_range']} 分。",
                  f"- 折算容差：{m3['tolerance']}",
                  f"- 锚点来源：{info['anchor_source']}"
                  f"（数据年份 {info['data_year']}）；数据质量：{quality}"]
        if info.get("proxy_schools"):
            lines.append(f"- 代理学校：{'、'.join(info['proxy_schools'])}")
        lines += ["", "使用锚点（校排名 ↔ 省排名）：", ""]
        lines += _table(["锚点线", "校排名", "省排名"],
                        [[a["name"], a["school_rank"], a["prov_rank"]]
                         for a in info["anchors_used"]])
        lines += ["", f"> {m3['disclaimer']}", ""]
    return lines


def _tier_note(ctx) -> list:
    """1.3 成绩档位说明：参考位次、等效位次与 Δ 分档口径。"""
    m4, m5 = ctx["m4"], ctx["m5"]
    meta, stats = m4["meta"], m4["statistics"]
    th = meta["tier_thresholds"]
    return ["### 1.3 成绩档位说明", "",
            f"- 普通批参考位次：{ctx['ref_rank']} 位"
            f"（投档线数据年份：{meta['data_year']} 年）。",
            f"- 多元路径等效位次：{m5['meta']['reference_rank']} − 4000"
            f" = {m5['meta']['equivalent_rank']} 位"
            f"（三条路径统一修正，Δ 口径同普通批）。",
            f"- 分档口径（Δ = 院校最低位次 − 参考位次）："
            f"冲 {th['冲']}；稳 {th['稳']}；保 {th['保']}；"
            f"搜索区间 Δ∈[{meta['delta_range'][0]},{meta['delta_range'][1]}]。",
            f"- 搜索区间内院校概况：985 院校 {stats['total_985_in_range']} 所、"
            f"211 院校 {stats['total_211_in_range']} 所、"
            f"省内院校 {stats['total_in_province_in_range']} 所。", ""]


def _m4_section(ctx) -> list:
    m4 = ctx["m4"]
    meta = m4["meta"]
    caps = meta["tier_caps"]
    lines = ["## 二、目标院校推荐", "",
             f"> 数据年份：{meta['data_year']} 年投档线；"
             f"展示上限：冲 {caps['冲']} / 稳 {caps['稳']} / 保 {caps['保']}；"
             "某档无符合条件院校则留空，不硬凑。"
             + (f"已按再选科目（{'、'.join(meta.get('secondary_subjects') or [])}）"
                f"过滤不可报专业组 {meta.get('filtered_by_subject', 0)} 行。"
                if meta.get("secondary_subjects") else ""), ""]
    th = meta["tier_thresholds"]
    for i, tier in enumerate(("冲", "稳", "保"), start=1):
        lines += [f"### 2.{i} {tier}（{th[tier]}）", ""]
        entries = m4["recommendations"][tier]
        if not entries:
            lines += [EMPTY_TIER_NOTE, ""]
            continue
        lines += _table(
            ["院校", "层次", "专业组", "最低分", "最低位次", "Δ", "推荐度", "备注"],
            [[e["school_name"], e["school_level"] or "—",
              "、".join(g["major_group_name"] for g in e["major_groups"]) or "—",
              e["min_score"], e["min_rank"], _delta(e["delta"]),
              e["recommend_level"], e.get("remark") or "—"] for e in entries])
        lines.append("")
    lines += ["> ★★★ = 意向院校或专业倾向匹配；★★ = 其他符合条件院校。"
              "同院校多专业组展示最低分组（定档依据）与含目标专业组。", ""]
    return lines


def _provenance_lines(entries: list) -> list:
    """推断标注（规则一：数字必须可回溯；推断按推断展示）——
    逐校列出数据口径/来源（CSV notes 列），有空值字段时说明留空语义。"""
    noted = [(t, e) for t, e in entries if e.get("notes")]
    if not noted:
        return []
    lines = ["> 数据口径与来源（标注「参考/估算」的数字中，部分为基于公开信息"
             "的推断值，逐校口径如下；留空项 = 未核实，宁空勿编；正式数据以"
             "各校当年招生简章与官方公布为准）：", ">"]
    lines += [f"> - {e['school_name']}（{t}档）：{e['notes']}"
              for t, e in noted]
    lines.append("")
    return lines


def _qiangji_block(ctx) -> list:
    q = ctx["m5"]["qiangji"]
    lines = ["### 3.1 强基计划", ""]
    entries = [(t, q[t][0]) for t in ("冲", "稳", "保") if q[t]]
    if not entries:
        lines += ["> 按当前等效位次，搜索区间内无符合条件的强基院校："
                  "按规则留空，不硬凑。正式申报以当年招生简章为准。", ""]
    else:
        lines += _table(
            ["档位", "院校", "招生专业", "参考最低分", "参考位次", "Δ",
             "考核方式", "报名截止", "备注"],
            [[t, e["school_name"], e["major_name"],
              _num(e["min_admission_score"]), e["min_admission_rank"],
              _delta(e["delta"]), e["exam_method"] or "—",
              e["apply_deadline"] or "—",
              e.get("background_note", "")] for t, e in entries])
        lines.append("")
        lines += _provenance_lines(entries)
    lines += [f"> 政策提示：{q['policy_note']}", ""]
    need_bg = any("background_note" in e for _, e in entries)
    if need_bg:
        lines += ["> 背景材料建议：上述推荐已标注「需补充背景材料」。"
                  "建议尽早积累学科竞赛、科创研究、研究性学习或社会实践经历，"
                  "并保留证书与证明材料，申报时按各校简章要求提交。", ""]
    elif not (ctx.get("has_awards") or ctx.get("has_activities")):
        # 无奖项/活动（即使本次无强基推荐）→ 如实建议积累，不得误称"已有"
        lines += ["> 背景材料建议：当前无竞赛获奖或活动经历。若考虑强基/综评"
                  "路径，建议尽早积累学科竞赛、科创研究、研究性学习或社会"
                  "实践经历，并保留证书与证明材料。", ""]
    else:
        lines += ["> 背景材料建议：已有奖项或活动经历，申报时按各校简章"
                  "要求整理证书与证明材料。", ""]
    return lines


def _zongping_block(ctx) -> list:
    z = ctx["m5"]["zongping"]
    lines = ["### 3.2 综合评价", ""]
    entries = [(t, z[t][0]) for t in ("冲", "稳", "保") if z[t]]
    if not entries:
        lines += ["> 按当前等效位次，搜索区间内无符合条件的综合评价院校："
                  "按规则留空，不硬凑。", ""]
    else:
        lines += _table(
            ["档位", "院校", "所在省份", "推荐专业", "参考最低分", "参考位次",
             "Δ", "综合成绩比例", "报名窗口"],
            [[t, e["school_name"], e["province_location"],
              e["recommended_majors"], _num(e["min_admission_score"]),
              e["min_admission_rank"], _delta(e["delta"]),
              e["score_ratio"] or "—", e["application_window"] or "—"]
             for t, e in entries])
        lines.append("")
        lines += _provenance_lines(entries)
    return lines


def _gangao_block(ctx) -> list:
    g = ctx["m5"]["gangao"]
    lines = ["### 3.3 港澳院校", ""]
    if g.get("skip_reason"):
        lines += [f"> 本次未推荐港澳院校：{g['skip_reason']}", ""]
        return lines
    entries = [(t, g[t][0]) for t in ("冲", "稳", "保") if g[t]]
    if not entries:
        lines += ["> 按当前等效位次，搜索区间内无符合条件的港澳院校："
                  "按规则留空，不硬凑。", ""]
    else:
        lines += _table(
            ["档位", "院校", "地区", "对标层次（经验性）", "招生专业", "语言要求",
             "参考分", "参考位次", "Δ", "学费", "申请窗口"],
            [[t, e["school_name"], e["region"], e["school_level_cn"],
              e["majors_offered"], e["language_requirement"] or "—",
              _num(e["estimated_score"]), e["estimated_rank"],
              _delta(e["delta"]), e["tuition_fee"] or "—",
              e["application_window"] or "—"] for t, e in entries])
        lines.append("")
        lines += _provenance_lines(entries)
    return lines


def _m5_section(ctx) -> list:
    meta = ctx["m5"]["meta"]
    lines = ["## 三、多元化升学路径", ""]
    if meta["grade_note"]:
        lines += [f"> {meta['grade_note']}", ""]
    lines += [f"> 三条路径统一按等效位次（{meta['reference_rank']} − 4000"
              f" = {meta['equivalent_rank']} 位）分档，各档至多 1 所，"
              "留空不硬凑。", ""]
    lines += _qiangji_block(ctx)
    lines += _zongping_block(ctx)
    lines += _gangao_block(ctx)
    if meta["disclaimer"]:
        lines += [f"> {meta['disclaimer']}", ""]
    return lines


# 第四部分：按年级的客观行动建议与时间轴（纯静态模板文案，
# 不含任何产品、价格、营销、机构信息——通用升学规划常识）
_GRADE_ADVICE = {
    "高三": [
        "### 4.1 高三学年时间轴（当年申报）", "",
        *_table(["时间", "行动建议"], [
            ["9–10 月", "关注招飞、强基计划等政策动向；用历次大考校排名做成绩定位，"
                        "初步圈定冲稳保区间"],
            ["11–12 月", "完成高考报名；关注高校冬令营与综合评价招生启动信息"],
            ["次年 1–2 月", "以一模成绩校准定位；系统整理获奖证书、活动证明等背景材料"],
            ["次年 3 月", "强基计划简章陆续发布，对照入围条件筛选目标院校"],
            ["次年 4 月", "完成强基计划报名（每生限报一所，候选清单三选一）；"
                          "关注综合评价与港澳院校的申请窗口"],
            ["次年 5 月", "冲刺复习与查漏补缺；核对强基、综评、港澳院校的申请材料"
                          "与截止时间"],
            ["次年 6 月", "参加高考；考后按通知参加强基校测、综评校测；"
                          "出分后按冲稳保梯度完成志愿填报"],
            ["次年 7–8 月", "关注各批次录取结果与征集志愿信息"]]),
        "",
        "### 4.2 执行建议（高三）", "",
        "- 成绩定位：每次大考后更新校排名，按本方案口径动态校准目标区间。",
        "- 志愿结构：按冲、稳、保梯度排列志愿，保底志愿务必稳妥。",
        "- 路径协同：强基、综评、港澳与普通高校批互不影响，可并行准备，"
        "注意各自报名截止时间。",
        "- 信息核验：招生政策、报名时间以省教育考试院与高校本科招生网"
        "当年发布为准。", "",
    ],
    "高二": [
        "### 4.1 高二学年重点（规划建议）", "",
        "- 成绩定位：保持年级排名稳定，建立历次大考成绩档案，"
        "为高三定位与折算积累数据。",
        "- 背景积累：结合兴趣参加学科竞赛（数学、物理、化学、生物、信息学）"
        "或科创、研究性学习活动，服务强基计划与综合评价申报。",
        "- 目标探索：了解目标院校与专业的选科要求、培养方向，"
        "初步形成意向院校清单。",
        "- 港澳方向（如有意愿）：保持英语单科优势，"
        "关注港澳高校内地招生宣讲信息。", "",
        "### 4.2 高二学年时间轴", "",
        *_table(["时间", "行动建议"], [
            ["上学期", "主攻选考科目薄弱环节；参加竞赛或科创项目"],
            ["寒假", "整理学期成绩与活动记录；研读目标院校往年招生简章"],
            ["下学期", "保持排名；参加研究性学习或社会实践，积累综合评价素材"],
            ["暑假", "参加高校夏令营、学科营；明确目标专业方向"]]),
        "",
    ],
    "高一": [
        "### 4.1 高一学年重点（规划建议）", "",
        "- 选科决策：了解本省选科模式，结合兴趣、学科优势与目标专业的"
        "选科要求确定选考科目，选科直接决定可报专业范围。",
        "- 习惯与基础：重视语文、数学、外语基础学科，适应高中学习节奏，"
        "关注年级排名变化。",
        "- 兴趣探索：广泛参加社团、竞赛体验与志愿活动，发现优势方向。", "",
        "### 4.2 高一学年时间轴", "",
        *_table(["时间", "行动建议"], [
            ["上学期", "适应高中节奏，各科均衡发展；初步了解选科政策"],
            ["寒假", "复盘首学期成绩；与任课教师沟通学科优势"],
            ["下学期", "按学校安排完成选科；开始记录大考校排名"],
            ["暑假", "预习选考科目；参加兴趣方向的夏令营或志愿活动"]]),
        "",
    ],
}


def _advice_section(ctx) -> list:
    lines = ["## 四、升学规划与执行建议", ""]
    lines += _GRADE_ADVICE[ctx["grade"]]
    return lines


def _sources_section(ctx) -> list:
    lines = ["## 数据来源与免责声明", "",
             "### 数据来源", ""]
    lines += _table(
        ["数据文件", "年份", "行数", "用途"],
        [[d["file"], "、".join(str(y) for y in d["years"]) or "—",
          d["rows"], d["usage"]] for d in ctx["data_sources"]])
    lines += ["",
              "> 行数为数据文件总行数；计算仅使用对应年份与科目组的记录，"
              "报告中院校数据均可追溯到数据文件对应行。",
              "",
              "### 免责声明", "",
              f"> {DISCLAIMER}",
              "> 数据每年 6–7 月更新，过期数据仅供参考。", ""]
    return lines


def render_report(ctx: dict) -> str:
    """把渲染上下文装配为 Markdown 报告全文（四部分 + 来源与免责）。

    所有院校名、专业名、分数、位次、学费直接取自引擎返回的 dict，
    本函数只做排版，不做任何院校信息的二次加工（spec §4.5）。
    """
    name = ctx.get("name")
    title = f"{name}的升学规划方案" if name else "升学规划方案"
    ai_reminder = "> ⚠️ 本方案为 AI 基于历史公开数据生成，仅供参考。"
    lines = [f"# {title}（{ctx['province']}{ctx['subject_group']}组）", "",
             f"> 生成日期：{ctx['date']} ｜ {DISCLAIMER}", "",
             ai_reminder, "",
             "## 一、基本信息与成绩定位", "",
             "### 1.1 输入摘要", ""]
    lines += _input_summary(ctx)
    lines.append("")
    lines += _positioning(ctx)
    lines += _tier_note(ctx)
    lines += _m4_section(ctx)
    lines += [ai_reminder, ""]
    lines += _m5_section(ctx)
    lines += [ai_reminder, ""]
    lines += _advice_section(ctx)
    lines += _sources_section(ctx)
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- 输出与扫描

def _section_summary(md: str) -> list:
    """按二级章节统计行数（标题行计入该章节），供 stdout JSON 摘要。"""
    sections = []
    current = None
    for line in md.splitlines():
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "lines": 0}
            sections.append(current)
        if current is not None:
            current["lines"] += 1
    return sections


def default_output_path(name) -> str:
    safe = "".join(c for c in (name or "方案") if c not in '/\\:*?"<>|').strip()
    return os.path.join("output",
                        f"{safe or '方案'}_升学方案_{date.today():%Y%m%d}.md")


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceReportInputError("JSON 包含重复字段")
        value[key] = item
    return value


def _reject_json_constant(_value):
    raise EvidenceReportInputError("JSON 包含非有限数值")


def _strict_json_text(text: str, label: str):
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, EvidenceReportInputError) as error:
        raise EvidenceReportInputError(f"{label} 不是严格 JSON") from error


def _strict_json_file(path: Path, label: str):
    try:
        before = path.stat()
        if not path.is_file() or path.is_symlink() or before.st_size > 1024 * 1024:
            raise EvidenceReportInputError(f"{label} 文件不安全")
        payload = path.read_bytes()
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            raise EvidenceReportInputError(f"{label} 文件读取期间发生变化")
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceReportInputError(f"{label} 不是 UTF-8") from error
    except OSError as error:
        raise EvidenceReportInputError(f"{label} 无法安全读取") from error
    return _strict_json_text(text, label)


def _validated_evidence_snapshot(bundle: Path):
    """Return only validate_evidence's public authenticated bundle snapshot."""

    result = validate_bundle_snapshot(bundle)
    if result.snapshot is None or result.issues:
        raise EvidenceReportInputError("证据包未通过完整性与来源门禁")
    return result.snapshot


def _profile_collection(payload: dict, name: str) -> tuple[str, ...]:
    value = payload[name]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvidenceReportInputError(f"画像字段 {name} 必须是字符串数组")
    try:
        return tuple(validate_profile_text(item, name) for item in value)
    except (TypeError, ValueError) as error:
        raise EvidenceReportInputError(f"画像字段 {name} 包含隐私或不安全文本") from error


def _load_public_profile(path: Path):
    payload = _strict_json_file(path, "用户画像")
    fields = {
        "schema_version",
        "province",
        "subject_mode",
        "subject_group",
        "secondary_subjects",
        "rank",
        "grade",
        "current_year",
        "target_major_categories",
        "target_cities",
        "target_schools",
        "eligibility_facts",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise EvidenceReportInputError("用户画像字段不符合公开契约")
    if payload["schema_version"] != "1.0":
        raise EvidenceReportInputError("用户画像版本不受支持")
    try:
        report_profile = StudentProfile(
            province=payload["province"],
            subject_mode=payload["subject_mode"],
            subject_group=payload["subject_group"],
            secondary_subjects=_profile_collection(payload, "secondary_subjects"),
            rank=payload["rank"],
            grade=payload["grade"],
            current_year=payload["current_year"],
        )
        recommendation_profile = RecommendationProfile(
            rank=payload["rank"],
            target_province=payload["province"],
            subject_group=payload["subject_group"],
            secondary_subjects=frozenset(_profile_collection(payload, "secondary_subjects")),
            target_major_categories=_profile_collection(payload, "target_major_categories"),
            target_cities=_profile_collection(payload, "target_cities"),
            target_schools=_profile_collection(payload, "target_schools"),
        )
        pathway_profile = PathwayProfile(
            rank=payload["rank"],
            province=payload["province"],
            subject_mode=payload["subject_mode"],
            current_year=payload["current_year"],
            eligibility_facts=_profile_collection(payload, "eligibility_facts"),
        )
    except (TypeError, ValueError) as error:
        raise EvidenceReportInputError("用户画像值不符合公开契约") from error
    return report_profile, recommendation_profile, pathway_profile


def _resolve_public_dataset(dataset: Path, profile: StudentProfile):
    try:
        resolved = dataset.resolve(strict=True)
    except OSError as error:
        raise EvidenceReportInputError("数据目录不存在") from error
    validation = validate_dataset_snapshot(resolved)
    if validation.issues or validation.snapshot is None:
        raise EvidenceReportInputError("数据目录未通过省份数据校验")
    try:
        config = validation.snapshot.config
        if config.province != profile.province or config.mode != profile.subject_mode:
            raise EvidenceReportInputError("用户画像与省份数据配置不匹配")
        validation.snapshot.validate_subjects(
            profile.subject_group,
            profile.secondary_subjects,
        )
    except EvidenceReportInputError:
        raise
    except Exception as error:
        raise EvidenceReportInputError("省份或选科配置无效") from error
    return validation.snapshot


_ADMISSION_FACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ADMISSION_VALUE_FIELDS = {
    "year",
    "province",
    "subject_group",
    "school_code",
    "program_group",
    "remarks",
    "min_score",
    "min_rank",
    "coverage_min_rank",
    "coverage_max_rank",
}


def _strict_admission_fact(record):
    if not isinstance(record, dict):
        return None
    field = record.get("field")
    if not isinstance(field, str) or not field.startswith("admission_record:"):
        return None
    suffix = field.removeprefix("admission_record:")
    if _ADMISSION_FACT_ID.fullmatch(suffix) is None:
        return None
    value = record.get("value")
    if not isinstance(value, dict) or set(value) != _ADMISSION_VALUE_FIELDS:
        return None
    status = record.get("status")
    if status not in {
        EvidenceStatus.OFFICIAL.value,
        EvidenceStatus.CORROBORATED.value,
        EvidenceStatus.REFERENCE.value,
    }:
        return None
    for name in ("year", "min_score", "min_rank", "coverage_min_rank", "coverage_max_rank"):
        if not isinstance(value[name], int) or isinstance(value[name], bool) or value[name] < 1:
            return None
    if value["year"] < 2000 or value["year"] > 2100:
        return None
    if value["coverage_min_rank"] > value["coverage_max_rank"]:
        return None
    if not value["coverage_min_rank"] <= value["min_rank"] <= value["coverage_max_rank"]:
        return None
    for name in ("province", "subject_group", "school_code", "program_group", "remarks"):
        if not isinstance(value[name], str):
            return None
        if name != "remarks" and (not value[name] or value[name] != value[name].strip()):
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in value[name]):
            return None
    source_ids = record.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids:
        return None
    if len(source_ids) != len(set(source_ids)) or any(
        not isinstance(source_id, str) or _ADMISSION_FACT_ID.fullmatch(source_id) is None
        for source_id in source_ids
    ):
        return None
    key = (
        value["year"],
        value["province"],
        value["subject_group"],
        value["school_code"],
        value["program_group"],
        value["remarks"],
    )
    return key, value, status, tuple(sorted(source_ids))


def _admission_fact_index(facts):
    index = {}
    conflicted = set()
    for record in facts:
        parsed = _strict_admission_fact(record)
        if parsed is None:
            continue
        key, value, status, source_ids = parsed
        snapshot = (value, status, source_ids)
        if key in index and index[key] != snapshot:
            conflicted.add(key)
        else:
            index[key] = snapshot
    for key in conflicted:
        index.pop(key, None)
    return index


def _public_recommendations(
    admission_rows: tuple[ValidatedAdmissionRow, ...],
    profile: RecommendationProfile,
    facts,
):
    """Run Task 3 without assigning unscoped facts to admission rows.

    v1 evidence facts must name the exact normalized admission field before a
    row can carry numeric provenance.  The current public replay fixture has a
    deliberately generic fact, so rows degrade to missing rather than gaining
    fabricated source coverage.
    """

    try:
        if not isinstance(admission_rows, tuple) or not all(
            isinstance(row, ValidatedAdmissionRow) for row in admission_rows
        ):
            raise TypeError("admission rows must come from validated snapshot")
        rows = [row.to_dict() for row in admission_rows]
        matching_years = [
            row["year"]
            for row in rows
            if row.get("subject_group") == profile.subject_group
        ]
        if not matching_years:
            raise DataError("已验证投档数据没有匹配的科目组")
        latest_year = max(matching_years)
        rows = [
            row for row in rows
            if row.get("subject_group") == profile.subject_group
            and row.get("year") == latest_year
        ]
        evidence_by_row = _admission_fact_index(facts)
        bounded_rows = []
        for original in rows:
            row = dict(original)
            key = (
                row.get("year"),
                row.get("province"),
                row.get("subject_group"),
                row.get("school_code"),
                row.get("program_group") or row.get("major_group_name"),
                row.get("remarks") or "",
            )
            accepted = evidence_by_row.get(key)
            if accepted is None:
                row.update(
                    {
                        "evidence_status": EvidenceStatus.MISSING.value,
                        "source_ids": (),
                        "coverage_min_rank": None,
                        "coverage_max_rank": None,
                    }
                )
            else:
                value, status, source_ids = accepted
                if row.get("min_score") != value["min_score"] or row.get("min_rank") != value["min_rank"]:
                    row.update(
                        {
                            "evidence_status": EvidenceStatus.CONFLICT.value,
                            "source_ids": (),
                            "coverage_min_rank": None,
                            "coverage_max_rank": None,
                        }
                    )
                else:
                    row.update(
                        {
                            "evidence_status": status,
                            "source_ids": source_ids,
                            "coverage_min_rank": value["coverage_min_rank"],
                            "coverage_max_rank": value["coverage_max_rank"],
                        }
                    )
            bounded_rows.append(row)
        return recommend_schools(bounded_rows, profile)
    except (DataError, SchoolRecommendError, TypeError, ValueError) as error:
        raise EvidenceReportInputError("普通批数据无法形成安全推荐结果") from error


def _build_evidence_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从显式省份数据、匿名画像和已验证证据包生成确定性 Markdown"
    )
    parser.add_argument("--dataset", required=True, type=Path, help="显式省份数据目录")
    parser.add_argument("--profile", required=True, type=Path, help="匿名严格 JSON 用户画像")
    parser.add_argument("--evidence", required=True, type=Path, help="已完成的证据包目录")
    parser.add_argument("--output", type=Path, default=None, help="可选 Markdown 输出路径")
    return parser


def _evidence_main(argv) -> int:
    if sys.version_info < (3, 10):
        print("缺少能力：需要 Python 3.10 或更高版本", file=sys.stderr)
        return 3
    args = _build_evidence_parser().parse_args(argv)
    try:
        report_profile, recommendation_profile, pathway_profile = _load_public_profile(
            args.profile
        )
        dataset = _resolve_public_dataset(args.dataset, report_profile)
        evidence = _validated_evidence_snapshot(args.evidence)
        facts = tuple(record.to_dict() for record in evidence.facts)
        recommendations = _public_recommendations(
            dataset.admission_rows,
            recommendation_profile,
            facts,
        )
        # The public replay fixture carries no policy records or versioned rank
        # anchors.  Task 5 is still entered through its new API; Task 4 remains
        # explicitly unavailable instead of falling back to bundled xibao data.
        pathways = evaluate_pathways(pathway_profile, (), model=None)
        model = build_report_model(
            report_profile,
            recommendations,
            rank=None,
            pathways=pathways,
            evidence=evidence,
        )
        markdown = render_markdown(model)
        hit = find_price_text(markdown)
        if hit is not None:
            raise EvidenceReportInputError("报告未通过合规扫描")
        if args.output is not None:
            output = args.output.resolve(strict=False)
            if (
                output.suffix.lower() != ".md"
                or not output.parent.is_dir()
                or output.exists()
            ):
                raise EvidenceReportInputError("输出路径必须位于现有目录且使用 .md 后缀")
            with output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(markdown)
    except EvidenceReportCapabilityError as error:
        print(f"缺少能力：{error}", file=sys.stderr)
        return 3
    except (EvidenceReportInputError, OSError, TypeError, ValueError) as error:
        print(f"错误[REPORT_002]：{error}", file=sys.stderr)
        return 2
    print(markdown, end="")
    return 0


def _uses_evidence_cli(argv) -> bool:
    return any(
        item in {"--dataset", "--profile", "--evidence"}
        for item in argv
    )


def main(argv=None) -> int:
    _reconfigure_utf8()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if _uses_evidence_cli(raw_argv):
        return _evidence_main(raw_argv)
    args = build_parser().parse_args(raw_argv)
    try:
        ctx = collect_context(args)
    except (DataError, RankCalcError, SchoolRecommendError,
            PathRecommendError) as e:
        code = getattr(e, "code", "DATA_001")
        print(f"错误[{code}]：{e}", file=sys.stderr)
        return 2

    md = render_report(ctx)
    output = args.output or default_output_path(args.name)
    out_dir = os.path.dirname(output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(md)

    # AC7（Markdown 侧）：全文价格/营销词确定性扫描，命中即拒绝交付
    hit = find_price_text(md)
    if hit is not None:
        os.remove(output)
        print(f"合规扫描未通过：报告含价格/营销词片段「{hit}」，"
              f"已删除报告文件，拒绝交付。", file=sys.stderr)
        return 2

    summary = {
        "report_path": output,
        "title": f"{ctx['name']}的升学规划方案" if ctx.get("name") else "升学规划方案",
        "generated_at": ctx["date"],
        "method": ctx["method"],
        "reference_rank": ctx["ref_rank"],
        "sections": _section_summary(md),
        "data_sources": ctx["data_sources"],
        "compliance": {"scanned": True, "hit": None},
        "input": {
            "province": args.province, "subject_group": args.subject_group,
            "grade": args.grade, "score": args.score, "rank": args.rank,
            "school": args.school, "exam_rank": args.exam_rank,
            "hkmo_willingness": args.hkmo_willingness,
            "has_awards": args.has_awards, "has_activities": args.has_activities,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
