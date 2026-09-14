"""Explicit material-to-symbol rules shared by CAD and PDF generation.

The source Excel value is never modified here.  Lookup accepts whitespace and
fullwidth Latin letters/digits, but deliberately preserves enclosed characters.

To add a custom material, append a record to ``material_libraries/custom.json``::

    {"schema_version": 1, "symbols": [
        {"material": "EXAMPLE", "aliases": [],
         "prefix": "T", "inner": "B", "shape": "triangle"}
    ]}

``EXAMPLE`` is illustrative, not an enabled engineering-material rule.  Custom
rules add names; they cannot silently override a base name or an alias.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from .runtime_paths import resource_root


SHAPES = frozenset({"none", "circle", "square", "triangle", "diamond", "pentagon", "hexagon", "star"})
_FULLWIDTH_ALPHANUMERIC = str.maketrans({
    chr(code): chr(code - 0xFEE0)
    for start, end in ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A))
    for code in range(start, end + 1)
})
_ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def material_lookup_key(value: str) -> str:
    """Make a lookup-only key without NFKC-folding meaningful symbol glyphs."""
    if not isinstance(value, str):
        raise ValueError("材质名称必须是文字。")
    return re.sub(r"\s+", "", value.translate(_FULLWIDTH_ALPHANUMERIC)).translate(_ASCII_UPPER)


@dataclass(frozen=True)
class SymbolRule:
    material: str
    aliases: tuple[str, ...] = ()
    prefix: str = ""
    inner: str = ""
    shape: str = "none"

    def __post_init__(self) -> None:
        if not isinstance(self.material, str) or not material_lookup_key(self.material):
            raise ValueError("材质规则必须填写非空材质名称。")
        if not isinstance(self.aliases, tuple) or any(
            not isinstance(alias, str) or not material_lookup_key(alias) for alias in self.aliases
        ):
            raise ValueError(f"材质“{self.material}”的别名必须是非空文字列表。")
        if not isinstance(self.shape, str) or self.shape not in SHAPES:
            raise ValueError(f"材质“{self.material}”使用了不支持的外框形状：{self.shape!r}。")
        for label, value in (("前置文字", self.prefix), ("框内文字", self.inner)):
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z]{0,3}", value) is None:
                raise ValueError(f"材质“{self.material}”的{label}只允许 0～3 个英文字母。")
        if self.shape == "none" and (self.prefix or self.inner):
            raise ValueError(f"材质“{self.material}”配置为无记号时，前置文字和框内文字必须为空。")

    @property
    def is_unmarked(self) -> bool:
        return self.shape == "none"

    @property
    def block_name(self) -> str:
        """Reuse a block when distinct material grades have the same appearance."""
        geometry = json.dumps([self.prefix, self.inner, self.shape], ensure_ascii=True, separators=(",", ":"))
        return "MDG_MAT_" + hashlib.sha256(geometry.encode("ascii")).hexdigest()[:20].upper()

    def to_record(self) -> dict:
        return {
            "material": self.material,
            "aliases": list(self.aliases),
            "prefix": self.prefix,
            "inner": self.inner,
            "shape": self.shape,
        }


def _read_rules(path: Path) -> tuple[SymbolRule, ...]:
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            payload = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取材质库 {path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"材质库 {path} 的顶层必须是对象。")
    if set(payload) - {"schema_version", "symbols"}:
        raise ValueError(f"材质库 {path} 存在未识别的配置项。")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError(f"材质库 {path} 的 schema_version 必须为 1。")
    records = payload.get("symbols")
    if not isinstance(records, list):
        raise ValueError(f"材质库 {path} 的 symbols 必须是列表。")
    result = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict) or set(record) - {"material", "aliases", "prefix", "inner", "shape"}:
            raise ValueError(f"材质库 {path} 的第 {index} 条规则格式错误或包含未识别配置项。")
        if "material" not in record or "shape" not in record:
            raise ValueError(f"材质库 {path} 的第 {index} 条规则缺少 material 或 shape。")
        aliases = record.get("aliases", [])
        if not isinstance(aliases, list):
            raise ValueError(f"材质库 {path} 的第 {index} 条 aliases 必须是列表。")
        try:
            result.append(SymbolRule(
                material=record["material"],
                aliases=tuple(aliases),
                prefix=record.get("prefix", ""),
                inner=record.get("inner", ""),
                shape=record["shape"],
            ))
        except ValueError as exc:
            raise ValueError(f"材质库 {path} 的第 {index} 条规则无效：{exc}") from exc
    return tuple(result)


@dataclass(frozen=True)
class MaterialLibrary:
    rules: tuple[SymbolRule, ...]
    fingerprint: str
    _lookup: Mapping[str, SymbolRule]

    @classmethod
    def load(cls, base_path: Path | None = None, custom_path: Path | None = None) -> "MaterialLibrary":
        root = resource_root() / "material_libraries"
        base = Path(base_path) if base_path is not None else root / "base.json"
        custom = Path(custom_path) if custom_path is not None else root / "custom.json"
        rules = _read_rules(base)
        if custom_path is not None or custom.exists():
            rules += _read_rules(custom)
        lookup: dict[str, SymbolRule] = {}
        for rule in rules:
            for spelling in (rule.material, *rule.aliases):
                key = material_lookup_key(spelling)
                if key in lookup:
                    previous = lookup[key]
                    raise ValueError(
                        f"材质名称或别名“{spelling}”重复，关联“{previous.material}”与“{rule.material}”。"
                        "自定义材质只能追加，不能覆盖已有规则。"
                    )
                lookup[key] = rule
        canonical = json.dumps(
            [rule.to_record() for rule in rules], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(rules=rules, fingerprint=digest, _lookup=MappingProxyType(lookup))

    def resolve(self, value: str) -> SymbolRule:
        key = material_lookup_key(value)
        if not key:
            raise ValueError("材质为空，请在 Excel 中填写明确材质；无记号材质也需要填写，例如 SS400。")
        try:
            return self._lookup[key]
        except KeyError:
            raise ValueError(
                f"材质“{value}”未在基础库或自定义材质库中登记，请补充材质全称与对应符号规则。"
            ) from None
