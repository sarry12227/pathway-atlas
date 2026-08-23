# -*- coding: utf-8 -*-
"""合规扫描（spec AC7 Markdown 侧）：价格/营销词确定性扫描。

规则组移植自 shengxue-system/backend/app/services/painpoint.py 的 PRICE_RES
（只移植扫描规则，不移植痛点/钩子逻辑）：
1. 阿拉伯数字紧邻 元/折/块（"68折""30600元""3000块"）；
2. 阿拉伯数字紧邻 万/千（"3万""1.5万""3千"）；
3. ￥/¥ 前缀数字（"￥36000""¥8800"）；
4. 中文数字紧邻 折/元/万（"六八折""三万"）；
5. 价格语境词（仅需/优惠价/立减/原价）+ 数字（阿拉伯或中文）。

移植边界调整（防误伤，规则语义不变）：
- 数字+万/千 后接外币单位（港币/澳门元）时不命中——港澳院校学费是随包
  数据字段（spec §4.5 学费 100% 来自数据文件），不是机构产品价格；
  "30600元""3万元"等人民币价格表述照常命中；
- 院校层次（985/211）、分数、位次、年份、行数等普通数字本就不在规则内
  （后随 元/折/万/千/块 才命中），如"武汉大学 985""±2000位次"不命中。
"""
import re
from typing import Optional

# 外币单位：后接这些单位的"X万/X千"是学费等数据字段，不算价格泄漏
_FOREIGN_CURRENCY = r"(?:港币|澳门元)"

PRICE_RES = (
    # 规则 1：阿拉伯数字紧邻 元/折/块
    re.compile(r"\d+(?:\.\d+)?\s*[元折块]"),
    # 规则 2：阿拉伯数字紧邻 万/千（后接外币单位的学费数据除外）
    re.compile(r"\d+(?:\.\d+)?\s*[万千](?!\s*" + _FOREIGN_CURRENCY + r")"),
    # 规则 3：￥/¥ 前缀数字
    re.compile(r"[￥¥]\s*\d"),
    # 规则 4a：中文数字紧邻 折/元
    re.compile(r"[一二三四五六七八九十百千万两]+\s*[折元]"),
    # 规则 4b：中文数字紧邻 万（后接外币单位除外，同规则 2 边界）
    re.compile(r"[一二三四五六七八九十百千万两]+\s*万(?!\s*" + _FOREIGN_CURRENCY + r")"),
    # 规则 5：价格语境词 + 数字（阿拉伯或中文）
    re.compile(r"(?:仅需|优惠价|立减|原价)\s*[0-9一二三四五六七八九十百千万两]+"),
)


def find_price_text(text) -> Optional[str]:
    """返回首个价格/折扣的精确命中；未命中返回 None。"""
    if not text:
        return None
    s = str(text)
    hits = [m for rx in PRICE_RES if (m := rx.search(s))]
    if not hits:
        return None
    m = min(hits, key=lambda h: h.start())
    return m.group(0)


def contains_price_text(text) -> bool:
    """确定性价格/折扣数字校验：命中 PRICE_RES 任一规则即视为价格泄漏。"""
    return find_price_text(text) is not None


def main(argv=None) -> int:
    """CLI：扫描任意 Markdown/文本成稿（交付前合规门禁）。

    用法：python scripts/compliance_scan.py <文件路径>
    命中 → stderr 输出命中片段，退出码 2；未命中 → 退出码 0。
    """
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if not argv:
        argv = sys.argv[1:]
    if len(argv) != 1:
        print("用法：python scripts/compliance_scan.py <文件路径>", file=sys.stderr)
        return 2
    import os
    if not os.path.exists(argv[0]):
        print("错误：文件不存在", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        hit = find_price_text(f.read())
    if hit:
        print(f"合规扫描未通过：命中价格/营销词片段「{hit}」，"
              f"请移除后重新交付", file=sys.stderr)
        return 2
    print("合规扫描通过")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
