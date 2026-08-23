# -*- coding: utf-8 -*-
"""数据加载层：按省份+科目组+年份加载随包 CSV，含数据完整性自检。

自检原则（spec §4.5 / 任务票 AC）：行数为 0、缺科目组/年份、缺文件
一律明确报错并指明缺哪份数据，绝不静默返回空推荐。
"""
import csv
import json
import os
import warnings
from pathlib import Path

try:
    from scripts.province_registry import (
        ProvinceConfigError,
        ProvinceRegistryError,
        _DirectoryIdentity,
        _resolve_legacy_province_dir,
        resolve_province_dir,
    )
except ModuleNotFoundError:  # Direct ``python scripts/*.py`` compatibility.
    from province_registry import (  # type: ignore
        ProvinceConfigError,
        ProvinceRegistryError,
        _DirectoryIdentity,
        _resolve_legacy_province_dir,
        resolve_province_dir,
    )

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_ROOT = os.path.join(SKILL_ROOT, "data")

INT_FIELDS = {
    "yifenyiduan": ("year", "score", "rank", "cumulative_count"),
    "tou_dang": ("year", "min_score", "min_rank", "is_inside_hubei"),
    "schools": ("is_in_hubei",),
    "qiangji": ("year", "enrollment_plan", "min_admission_rank"),
    "zongping": ("year", "min_admission_rank"),
    "gangao": ("year", "estimated_rank", "interview_required"),
}

FLOAT_FIELDS = {
    "qiangji": ("min_admission_score",),
    "zongping": ("min_admission_score",),
    "gangao": ("estimated_score",),
}

# M5 多元路径三表（无科目组维度，样本按物理类整理，见 data/hubei/README.md）
PATH_TABLES = ("qiangji", "zongping", "gangao")


class DataError(Exception):
    """数据缺失/损坏的业务错误，message 必须指明缺哪份数据。"""


def get_province_dir(province: str, root: os.PathLike[str] | str = DEFAULT_DATA_ROOT) -> Path:
    """Deprecated name-based bridge; new callers pass an explicit province_dir.

    Strict v1 metadata is always attempted first.  The narrow fallback exists
    for one migration release and reads only the display name from the same
    safely scanned direct-child ``province.json`` files.
    """

    warnings.warn(
        "name-based province loading is deprecated; resolve once and pass province_dir",
        DeprecationWarning,
        stacklevel=2,
    )
    if not isinstance(province, str) or not province.strip():
        raise DataError("省份必填；请先解析省份数据目录")
    try:
        try:
            return resolve_province_dir(root, province.strip())
        except ProvinceConfigError:
            return _resolve_legacy_province_dir(root, province.strip())
    except ProvinceRegistryError as error:
        raise DataError(str(error)) from error


def _province_dir(province: str, root: os.PathLike[str] | str) -> str:
    """One-release compatibility alias for the legacy verification script."""

    return os.fspath(get_province_dir(province, root))


def _resolved_data_dir(
    province: str | None,
    root: os.PathLike[str] | str,
    province_dir: os.PathLike[str] | str | None,
) -> Path:
    if province_dir is not None:
        try:
            candidate = Path(province_dir)
            normalized = Path(os.path.abspath(os.fspath(candidate)))
            if not candidate.is_absolute() or candidate != normalized:
                raise DataError("province_dir 必须是调用方预先解析的绝对目录")
            return _DirectoryIdentity.capture(province_dir, "已解析省份数据目录").path
        except DataError:
            raise
        except (TypeError, ValueError) as error:
            raise DataError("province_dir 必须是调用方预先解析的绝对目录") from error
        except ProvinceRegistryError as error:
            raise DataError(str(error)) from error
    if province is None:
        raise DataError("必须提供显式 province_dir；旧调用方可暂时提供 province")
    return get_province_dir(province, root)


def _load_csv(province_dir: Path, table: str, province_label: str,
              int_fields=(), required: bool = True) -> list[dict]:
    path = province_dir / f"{table}.csv"
    if not path.exists():
        if not required:
            return []
        raise DataError(f"数据文件缺失：{path}（{province_label} {table}），"
                        f"请先按省份接入文档准备该数据文件")
    fields = int_fields or INT_FIELDS.get(table, ())
    float_fields = FLOAT_FIELDS.get(table, ())
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row = dict(raw)
            for field in fields:
                value = row.get(field)
                row[field] = int(value) if value not in (None, "") else None
            for field in float_fields:
                value = row.get(field)
                row[field] = float(value) if value not in (None, "") else None
            rows.append(row)
    if not rows and required:
        raise DataError(f"数据文件为空：{path}（{province_label} {table}），"
                        f"不输出空推荐，请先导入数据")
    return rows


def load_province_config(province: str | None = None, root: os.PathLike[str] | str = DEFAULT_DATA_ROOT,
                         require_anchors: bool = True, *,
                         province_dir: os.PathLike[str] | str | None = None) -> dict:
    """加载省份配置（province.json）：科目组、锚点线集合、当年判定、展示参数，
    以及 M4/M5 推荐参数（tier_thresholds/delta_range/tier_caps/equiv_rank_adjust，
    均可选，引擎侧缺省回退湖北默认值，见 school_recommend.params_from_config）。

    锚点线名称/数量/列映射全部来自配置，代码不假定具体锚点（spec §4.1）。
    require_anchors=True（默认，M3 估分路径）时 anchors 缺失/不完整明确报错；
    不需要估分的调用方（M4/M5、verify 脚本）传 False，只要求配置文件本身存在可解析。
    """
    directory = _resolved_data_dir(province, root, province_dir)
    label = province or directory.name
    path = directory / "province.json"
    if not path.exists():
        raise DataError(f"省份配置缺失：{path}（{label} province.json），"
                        f"请先按省份接入文档准备该配置文件")
    with path.open(encoding="utf-8") as f:
        config = json.load(f)
    if not require_anchors:
        return config
    anchors = config.get("anchors") or []
    if not anchors:
        raise DataError(f"省份配置 {path} 未定义任何锚点线（anchors 为空），"
                        f"无法进行校排名折算")
    for a in anchors:
        if not all(a.get(k) for k in ("name", "school_rank_col", "prov_rank_col")):
            raise DataError(f"省份配置 {path} 锚点定义不完整"
                            f"（需含 name/school_rank_col/prov_rank_col）：{a}")
    return config


def load_xibao(province: str | None, config: dict,
               root: os.PathLike[str] | str = DEFAULT_DATA_ROOT, *,
               province_dir: os.PathLike[str] | str | None = None) -> list[dict]:
    """加载喜报锚点数据；锚点列按省份配置解析为整数（配置驱动）。"""
    int_fields = {"year", "qingbei_count", "fenshu_600_count"}
    for a in config["anchors"]:
        int_fields.add(a["school_rank_col"])
        int_fields.add(a["prov_rank_col"])
    directory = _resolved_data_dir(province, root, province_dir)
    return _load_csv(directory, "xibao", province or directory.name,
                     int_fields=sorted(int_fields))


def load_schools(province: str | None = None, root: os.PathLike[str] | str = DEFAULT_DATA_ROOT, *,
                 province_dir: os.PathLike[str] | str | None = None) -> list[dict]:
    """加载高中名录（降级链"同城同档代理"定位用）；文件缺失时返回空表，
    不阻断折算（代理级会自动落到拒绝级）。"""
    directory = _resolved_data_dir(province, root, province_dir)
    return _load_csv(directory, "schools", province or directory.name, required=False)


def _latest(rows: list[dict], subject_group: str, table: str,
            province: str) -> tuple[int, list[dict]]:
    years = [r["year"] for r in rows if r["subject_group"] == subject_group]
    if not years:
        raise DataError(f"{province} {table} 数据无科目组「{subject_group}」的年份记录，"
                        f"不输出空推荐，请先导入对应数据")
    year = max(years)
    return year, [r for r in rows
                  if r["subject_group"] == subject_group and r["year"] == year]


def load_toudang(province: str | None = None, subject_group: str | None = None,
                 root: os.PathLike[str] | str = DEFAULT_DATA_ROOT, *,
                 province_dir: os.PathLike[str] | str | None = None) -> tuple[int, list[dict]]:
    """加载投档线：返回 (最新年份, 该年该科目组全部行)。"""
    if not isinstance(subject_group, str) or not subject_group.strip():
        raise DataError("科目组必填")
    directory = _resolved_data_dir(province, root, province_dir)
    label = province or directory.name
    rows = _load_csv(directory, "tou_dang", label)
    return _latest(rows, subject_group.strip(), "tou_dang", label)


def load_yifenyiduan(province: str | None = None, subject_group: str | None = None,
                     root: os.PathLike[str] | str = DEFAULT_DATA_ROOT, *,
                     province_dir: os.PathLike[str] | str | None = None) -> tuple[int, list[dict]]:
    """加载一分一段：返回 (最新年份, 该年该科目组全部行)。"""
    if not isinstance(subject_group, str) or not subject_group.strip():
        raise DataError("科目组必填")
    directory = _resolved_data_dir(province, root, province_dir)
    label = province or directory.name
    rows = _load_csv(directory, "yifenyiduan", label)
    return _latest(rows, subject_group.strip(), "yifenyiduan", label)


def load_path_table(province: str | None, table: str,
                    root: os.PathLike[str] | str = DEFAULT_DATA_ROOT, *,
                    province_dir: os.PathLike[str] | str | None = None) -> list[dict]:
    """加载 M5 路径表（qiangji/zongping/gangao）：返回全部年份行，
    最新年份选取在 path_recommend 内完成（口径同现有系统 MAX(year)）。"""
    if table not in PATH_TABLES:
        raise DataError(f"未知路径表「{table}」，应为：{'、'.join(PATH_TABLES)}")
    directory = _resolved_data_dir(province, root, province_dir)
    return _load_csv(directory, table, province or directory.name)


def score_to_rank(province: str | None, subject_group: str, score: int,
                  root: os.PathLike[str] | str = DEFAULT_DATA_ROOT, *,
                  province_dir: os.PathLike[str] | str | None = None) -> dict:
    """分数→省排反查（口径同现有系统 import 推导）：
    精确命中取该分累计人数；分数在表中缺档时取相邻低分的累计；
    低于表中最低分 → 明确报错。"""
    year, rows = load_yifenyiduan(province, subject_group, root=root,
                                  province_dir=province_dir)
    by_score = {r["score"]: r["rank"] for r in rows}
    if score in by_score:
        return {"score": score, "rank": by_score[score], "year": year}
    lower = [s for s in by_score if s < score]
    if not lower:
        label = province if province is not None else (
            Path(province_dir).name if province_dir is not None else "该省"
        )
        raise DataError(f"分数 {score} 低于{label}{subject_group}组{year}年"
                        f"一分一段表最低分，无法反查省排名，请改用 --rank 直接输入省排名")
    nearest = max(lower)
    return {"score": score, "rank": by_score[nearest], "year": year}
