"""Strict, metadata-driven discovery for province datasets.

Only a fixed ``province.json`` immediately below each direct child directory is
considered.  Metadata names identify datasets; metadata never supplies a path.
"""

from __future__ import annotations

import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProvinceRegistryError(ValueError):
    """Base class for controlled province-registry failures."""


class ProvincePathError(ProvinceRegistryError):
    """The registry root or a candidate path could not be trusted."""


class ProvinceConfigError(ProvinceRegistryError):
    """A province metadata document violates the public contract."""


class DuplicateProvinceError(ProvinceConfigError):
    """Two direct child datasets declare the same province name."""


class UnknownProvinceError(ProvinceRegistryError):
    """The requested province is absent from the discovered registry."""


class SubjectSelectionError(ProvinceRegistryError):
    """A selected subject group is invalid for a province configuration."""


_REPARSE_POINT = 0x0400
_SCHEMA_VERSION = "1.0"
_MODES = frozenset(("3+1+2", "3+3"))
_REQUIRED_FIELDS = frozenset(
    (
        "province",
        "mode",
        "primary_subjects",
        "secondary_subjects",
        "score_scale",
        "schema_version",
    )
)
_MAX_METADATA_BYTES = 256 * 1024


@dataclass(frozen=True)
class ProvinceConfig:
    """Validated, immutable province metadata plus its verified directory."""

    province: str
    mode: str
    primary_subjects: tuple[str, ...]
    secondary_subjects: tuple[str, ...]
    score_scale: int | float
    schema_version: str
    directory: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "province": self.province,
            "mode": self.mode,
            "primary_subjects": list(self.primary_subjects),
            "secondary_subjects": list(self.secondary_subjects),
            "score_scale": self.score_scale,
            "schema_version": self.schema_version,
            "directory": str(self.directory),
        }


@dataclass(frozen=True)
class _DirectoryIdentity:
    path: Path
    device: int
    inode: int
    attributes: int

    @classmethod
    def capture(cls, value: os.PathLike[str] | str, description: str) -> "_DirectoryIdentity":
        requested = Path(value)
        try:
            absolute = Path(os.path.abspath(os.fspath(requested)))
            info = os.lstat(absolute)
            resolved = absolute.resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ProvincePathError(f"{description}不可访问或不存在") from error
        attributes = getattr(info, "st_file_attributes", 0)
        if (
            resolved != absolute
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or attributes & _REPARSE_POINT
        ):
            raise ProvincePathError(f"{description}必须是真实目录，不能是链接或重解析点")
        return cls(resolved, info.st_dev, info.st_ino, attributes)

    def verify(self, description: str) -> None:
        try:
            info = os.lstat(self.path)
            resolved = self.path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ProvincePathError(f"{description}在扫描期间发生变化") from error
        attributes = getattr(info, "st_file_attributes", 0)
        if (
            resolved != self.path
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or attributes & _REPARSE_POINT
            or (info.st_dev, info.st_ino, attributes) != (self.device, self.inode, self.attributes)
        ):
            raise ProvincePathError(f"{description}在扫描期间发生变化")


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_metadata(root: _DirectoryIdentity, child: _DirectoryIdentity) -> dict[str, Any] | None:
    metadata = child.path / "province.json"
    try:
        before = os.lstat(metadata)
    except FileNotFoundError:
        child.verify("省份数据目录")
        root.verify("省份数据根目录")
        return None
    except OSError as error:
        raise ProvincePathError("province.json 无法安全读取") from error

    attributes = getattr(before, "st_file_attributes", 0)
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or attributes & _REPARSE_POINT
        or not _is_below(metadata, root.path)
    ):
        raise ProvincePathError("province.json 必须是数据根目录内的真实普通文件")
    if before.st_size > _MAX_METADATA_BYTES:
        raise ProvinceConfigError("province.json 超过允许的元数据大小")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(metadata, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ProvincePathError("province.json 在读取期间发生变化")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_METADATA_BYTES:
                    raise ProvinceConfigError("province.json 超过允许的元数据大小")
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    except ProvinceRegistryError:
        raise
    except OSError as error:
        raise ProvincePathError("province.json 无法安全读取") from error

    try:
        after = os.lstat(metadata)
    except OSError as error:
        raise ProvincePathError("province.json 在读取期间发生变化") from error
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        raise ProvincePathError("province.json 在读取期间发生变化")
    child.verify("省份数据目录")
    root.verify("省份数据根目录")

    try:
        text = b"".join(chunks).decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ProvinceConfigError("province.json 不是严格 UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ProvinceConfigError("province.json 顶层必须是对象")
    return payload


def _normalize_province(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProvinceConfigError("province 必须是非空字符串")
    province = value.strip()
    if province in (".", "..") or any(marker in province for marker in ("/", "\\", "\0")):
        raise ProvinceConfigError("province 只能是显示名称，不能包含路径语法")
    return province


def _normalize_subjects(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ProvinceConfigError(f"{field} 必须是非空字符串数组")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProvinceConfigError(f"{field} 只能包含非空字符串")
        subject = item.strip()
        if subject not in seen:
            seen.add(subject)
            normalized.append(subject)
    if not normalized:
        raise ProvinceConfigError(f"{field} 去空白去重后不能为空")
    return tuple(normalized)


def _parse_config(payload: dict[str, Any], directory: Path) -> ProvinceConfig:
    keys = frozenset(payload)
    if keys != _REQUIRED_FIELDS:
        missing = sorted(_REQUIRED_FIELDS - keys)
        extra = sorted(keys - _REQUIRED_FIELDS)
        details = []
        if missing:
            details.append("缺少字段：" + "、".join(missing))
        if extra:
            details.append("未知字段：" + "、".join(extra))
        raise ProvinceConfigError("province.json 字段不符合契约（" + "；".join(details) + "）")

    province = _normalize_province(payload["province"])
    mode = payload["mode"]
    if not isinstance(mode, str) or mode not in _MODES:
        raise ProvinceConfigError("mode 仅支持 3+1+2 或 3+3")
    primary = _normalize_subjects(payload["primary_subjects"], "primary_subjects")
    secondary = _normalize_subjects(payload["secondary_subjects"], "secondary_subjects")

    score_scale = payload["score_scale"]
    if (
        isinstance(score_scale, bool)
        or not isinstance(score_scale, (int, float))
        or not math.isfinite(score_scale)
        or not 100 <= score_scale <= 1000
    ):
        raise ProvinceConfigError("score_scale 必须是 100 到 1000 之间的有限正数")
    schema_version = payload["schema_version"]
    if not isinstance(schema_version, str) or schema_version != _SCHEMA_VERSION:
        raise ProvinceConfigError("不支持的 province schema_version（当前仅支持 1.0）")

    return ProvinceConfig(
        province=province,
        mode=mode,
        primary_subjects=primary,
        secondary_subjects=secondary,
        score_scale=score_scale,
        schema_version=schema_version,
        directory=directory,
    )


def _scan_metadata(root: os.PathLike[str] | str) -> tuple[_DirectoryIdentity, list[tuple[Path, dict[str, Any]]]]:
    root_identity = _DirectoryIdentity.capture(root, "省份数据根目录")
    documents: list[tuple[Path, dict[str, Any]]] = []
    try:
        with os.scandir(root_identity.path) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError as error:
        raise ProvincePathError("省份数据根目录无法扫描") from error
    for entry in entries:
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise ProvincePathError("省份数据根目录包含无法验证的条目") from error
        attributes = getattr(info, "st_file_attributes", 0)
        if entry.is_symlink() or attributes & _REPARSE_POINT:
            raise ProvincePathError("省份数据根目录不能包含链接或重解析点")
        if not stat.S_ISDIR(info.st_mode):
            continue
        child = _DirectoryIdentity.capture(Path(entry.path), "省份数据目录")
        if not _is_below(child.path, root_identity.path):
            raise ProvincePathError("省份数据目录越出数据根目录")
        payload = _read_metadata(root_identity, child)
        if payload is not None:
            documents.append((child.path, payload))
    root_identity.verify("省份数据根目录")
    return root_identity, documents


def discover_provinces(root: os.PathLike[str] | str) -> dict[str, ProvinceConfig]:
    """Discover strict province metadata from direct child directories only."""

    _root, documents = _scan_metadata(root)
    discovered: dict[str, ProvinceConfig] = {}
    for directory, payload in documents:
        config = _parse_config(payload, directory)
        if config.province in discovered:
            raise DuplicateProvinceError(f"省份名称重复：{config.province}")
        discovered[config.province] = config
    return dict(sorted(discovered.items()))


def resolve_province_dir(root: os.PathLike[str] | str, province: str) -> Path:
    """Resolve a dataset by metadata display name, never by path concatenation."""

    discovered = discover_provinces(root)
    if not isinstance(province, str) or province not in discovered:
        available = "、".join(sorted(discovered)) or "无"
        raise UnknownProvinceError(f"未知省份「{province}」；可用省份：{available}")
    config = discovered[province]
    identity = _DirectoryIdentity.capture(config.directory, "省份数据目录")
    root_identity = _DirectoryIdentity.capture(root, "省份数据根目录")
    if not _is_below(identity.path, root_identity.path):
        raise ProvincePathError("省份数据目录越出数据根目录")
    return identity.path


def _resolve_legacy_province_dir(root: os.PathLike[str] | str, province: str) -> Path:
    """Narrow one-release bridge for old metadata that only declared province."""

    root_identity, documents = _scan_metadata(root)
    matched: Path | None = None
    names: list[str] = []
    for directory, payload in documents:
        name = _normalize_province(payload.get("province"))
        if name in names:
            raise DuplicateProvinceError(f"省份名称重复：{name}")
        names.append(name)
        if name == province:
            matched = directory
    if matched is None:
        available = "、".join(sorted(names)) or "无"
        raise UnknownProvinceError(f"未知省份「{province}」；可用省份：{available}")
    identity = _DirectoryIdentity.capture(matched, "省份数据目录")
    root_identity.verify("省份数据根目录")
    if not _is_below(identity.path, root_identity.path):
        raise ProvincePathError("省份数据目录越出数据根目录")
    return identity.path


def _normalize_selection(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SubjectSelectionError(f"{field}必须是一个非空科目名称")
    return value.strip()


def validate_subject_selection(
    config: ProvinceConfig,
    primary: str,
    secondary: tuple[str, ...] | list[str],
) -> None:
    """Validate a complete subject selection according to the declared mode."""

    if not isinstance(config, ProvinceConfig):
        raise SubjectSelectionError("省份配置无效")
    primary_value = _normalize_selection(primary, "首选科目")
    if isinstance(secondary, (str, bytes)) or not isinstance(secondary, (tuple, list)):
        raise SubjectSelectionError("再选科目必须是科目列表")
    secondary_values = tuple(_normalize_selection(item, "再选科目") for item in secondary)
    if len(secondary_values) != 2:
        raise SubjectSelectionError("必须选择恰好两个再选科目")
    if len(set(secondary_values)) != 2:
        raise SubjectSelectionError("三个选科必须互不重复")

    if config.mode == "3+1+2":
        if primary_value not in config.primary_subjects:
            choices = "、".join(config.primary_subjects)
            raise SubjectSelectionError(f"首选科目无效；可选：{choices}")
        unknown = sorted(set(secondary_values) - set(config.secondary_subjects))
        if unknown:
            choices = "、".join(config.secondary_subjects)
            raise SubjectSelectionError(f"再选科目无效；可选：{choices}")
        if primary_value in secondary_values:
            raise SubjectSelectionError("三个选科必须互不重复")
        return

    allowed = set(config.primary_subjects) | set(config.secondary_subjects)
    selection = (primary_value,) + secondary_values
    if len(set(selection)) != 3:
        raise SubjectSelectionError("3+3 模式必须选择三个不同科目")
    unknown = sorted(set(selection) - allowed)
    if unknown:
        choices = "、".join(sorted(allowed))
        raise SubjectSelectionError(f"3+3 选科包含未配置科目；可选：{choices}")
