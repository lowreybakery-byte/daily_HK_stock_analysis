from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


@dataclass
class StockIdentity:
    code: str
    canonical_name: str
    market: str = "CN"
    aliases: Optional[List[str]] = None

    def all_names(self) -> List[str]:
        names = [self.canonical_name]
        if self.aliases:
            names.extend([x for x in self.aliases if x])
        return list(dict.fromkeys(names))


class StockNameResolver:
    """
    作用：
    1. 根据股票代码，返回“标准中文股票名”
    2. 校验 AI 输出中的股票名称是否与代码匹配
    3. 在最终文本中，强制把标题/括号中的股票名修正为标准名

    说明：
    - 当前默认内置了一些常见股票映射，方便先跑起来
    - 后续你可以把 mapping 扩展成从 csv / json / 数据接口自动加载
    """

    def __init__(self, mapping: Optional[Dict[str, StockIdentity]] = None):
        self.mapping: Dict[str, StockIdentity] = mapping or self._default_mapping()

    # ========= 对外主方法 =========

    def normalize_code(self, code: str) -> str:
        """
        规范化股票代码：
        - 去空格
        - 去市场后缀（如 .SH / .SZ / .HK）
        - 保留纯数字
        """
        if code is None:
            return ""

        code = str(code).strip().upper()
        code = code.replace("SH", "").replace("SZ", "").replace("HK", "")
        code = code.replace(".", "").strip()

        digits = re.sub(r"\D", "", code)
        return digits

    def resolve(self, code: str) -> Optional[StockIdentity]:
        code = self.normalize_code(code)
        return self.mapping.get(code)

    def resolve_many(self, codes: Iterable[str]) -> List[StockIdentity]:
        result: List[StockIdentity] = []
        for code in codes:
            item = self.resolve(code)
            if item:
                result.append(item)
        return result

    def get_canonical_name(self, code: str, fallback: str = "") -> str:
        item = self.resolve(code)
        if item:
            return item.canonical_name
        return fallback

    def is_name_matching_code(self, code: str, candidate_name: str) -> bool:
        """
        判断某个中文股票名是否和代码匹配
        """
        if not candidate_name:
            return False

        item = self.resolve(code)
        if not item:
            return False

        candidate_name = self._clean_name(candidate_name)
        valid_names = [self._clean_name(x) for x in item.all_names()]

        return candidate_name in valid_names

    def build_strict_identity_prompt(self, code: str) -> str:
        """
        给大模型的强约束提示词片段
        """
        item = self.resolve(code)
        if not item:
            return f"股票代码：{self.normalize_code(code)}。请只围绕该代码分析，禁止臆造股票名称。"

        return (
            f"你现在分析的股票唯一身份信息如下：\n"
            f"股票代码：{item.code}\n"
            f"股票名称：{item.canonical_name}\n"
            f"市场：{item.market}\n\n"
            f"严格要求：\n"
            f"1. 全文只能使用“{item.canonical_name}”作为股票名称。\n"
            f"2. 禁止写成其他公司的名字。\n"
            f"3. 若不确定，请重复使用股票代码 {item.code}，不要猜测别的名称。\n"
            f"4. 标题、摘要、正文、结论中的股票名称必须与代码一致。\n"
        )

    def repair_text(self, code: str, text: str) -> str:
        """
        对 AI 输出结果做一次“保底修正”：
        - 如果识别到 “某某（600519）” 这种结构，但名字不对，就改成标准名
        - 如果识别到 “股票名称：xxx” 这种结构，也会替换成标准名
        - 如果标题首行里有“某某 600519 分析”，也尽量修正

        注意：
        这是保底修复，不是万能 NLP。
        但对大多数报告格式已经够实用。
        """
        if not text:
            return text

        item = self.resolve(code)
        if not item:
            return text

        canonical = item.canonical_name
        norm_code = item.code

        repaired = text

        # 1) 修正：xxx（600519）
        repaired = re.sub(
            rf"([^\n（）()：:【】\[\] ]{{2,20}})\s*[（(]\s*{re.escape(norm_code)}\s*[）)]",
            f"{canonical}（{norm_code}）",
            repaired,
        )

        # 2) 修正：股票名称：xxx
        repaired = re.sub(
            r"(股票名称\s*[：:]\s*)([^\n]+)",
            rf"\1{canonical}",
            repaired,
        )

        # 3) 修正：个股名称：xxx
        repaired = re.sub(
            r"(个股名称\s*[：:]\s*)([^\n]+)",
            rf"\1{canonical}",
            repaired,
        )

        # 4) 修正：标题中类似 “XXX 600519 分析”
        repaired = re.sub(
            rf"(^|\n)([^\n\d]{{2,20}})\s+{re.escape(norm_code)}(\s*(?:分析|复盘|点评|报告))",
            rf"\1{canonical} {norm_code}\3",
            repaired,
        )

        # 5) 修正：如果正文中出现 “XXX股份（600519）/ XXX集团（600519）” 等
        repaired = re.sub(
            rf"[^\n（）()]{2,30}[（(]\s*{re.escape(norm_code)}\s*[）)]",
            f"{canonical}（{norm_code}）",
            repaired,
        )

        return repaired

    def validate_or_raise(self, code: str, candidate_name: str) -> None:
        """
        如果名称与代码不匹配，直接抛错
        """
        if not self.is_name_matching_code(code, candidate_name):
            item = self.resolve(code)
            expected = item.canonical_name if item else "未知"
            raise ValueError(
                f"股票名称与代码不匹配：code={self.normalize_code(code)}, "
                f"candidate_name={candidate_name}, expected={expected}"
            )

    # ========= 可选辅助方法 =========

    def upsert_identity(
        self,
        code: str,
        canonical_name: str,
        market: str = "CN",
        aliases: Optional[List[str]] = None,
    ) -> None:
        norm_code = self.normalize_code(code)
        self.mapping[norm_code] = StockIdentity(
            code=norm_code,
            canonical_name=canonical_name.strip(),
            market=market,
            aliases=aliases or [],
        )

    def extract_name_candidates(self, text: str) -> List[str]:
        """
        从文本中粗略提取可能的股票名候选，仅用于调试辅助
        """
        if not text:
            return []

        patterns = [
            r"股票名称\s*[：:]\s*([^\n]+)",
            r"个股名称\s*[：:]\s*([^\n]+)",
            r"([^\n（）()：:【】\[\] ]{2,20})\s*[（(]\s*\d{5,6}\s*[）)]",
        ]

        candidates: List[str] = []
        for p in patterns:
            matches = re.findall(p, text)
            for m in matches:
                m = self._clean_name(m)
                if m:
                    candidates.append(m)

        return list(dict.fromkeys(candidates))

    # ========= 内部工具 =========

    def _clean_name(self, name: str) -> str:
        if not name:
            return ""
        name = str(name).strip()
        name = re.sub(r"\s+", "", name)
        name = name.replace("Ａ", "A").replace("Ｂ", "B")
        return name

    def _default_mapping(self) -> Dict[str, StockIdentity]:
        """
        先内置一些常见股票。
        你后面可以继续补充。
        如果你的 STOCK_LIST 里有更多股票，就把它们加到这里。
        """
        raw = [
            StockIdentity("600519", "贵州茅台", "CN", ["茅台"]),
            StockIdentity("000001", "平安银行", "CN"),
            StockIdentity("600036", "招商银行", "CN"),
            StockIdentity("600276", "恒瑞医药", "CN"),
            StockIdentity("601318", "中国平安", "CN"),
            StockIdentity("600887", "伊利股份", "CN"),
            StockIdentity("600309", "万华化学", "CN"),
            StockIdentity("002594", "比亚迪", "CN"),
            StockIdentity("300750", "宁德时代", "CN"),
            StockIdentity("601899", "紫金矿业", "CN"),
            StockIdentity("600000", "浦发银行", "CN"),
            StockIdentity("601398", "工商银行", "CN"),
            StockIdentity("601288", "农业银行", "CN"),
            StockIdentity("601988", "中国银行", "CN"),
            StockIdentity("601939", "建设银行", "CN"),
            StockIdentity("600030", "中信证券", "CN"),
            StockIdentity("300059", "东方财富", "CN"),
            StockIdentity("600900", "长江电力", "CN"),
            StockIdentity("000858", "五粮液", "CN"),
            StockIdentity("002415", "海康威视", "CN"),
            StockIdentity("600031", "三一重工", "CN"),
            StockIdentity("601012", "隆基绿能", "CN", ["隆基股份"]),
            StockIdentity("688981", "中芯国际", "CN"),
            StockIdentity("600941", "中国移动", "CN"),
            StockIdentity("601857", "中国石油", "CN"),
            StockIdentity("600028", "中国石化", "CN"),
            StockIdentity("601088", "中国神华", "CN"),
            StockIdentity("601668", "中国建筑", "CN"),
            StockIdentity("600104", "上汽集团", "CN"),
            StockIdentity("000333", "美的集团", "CN"),
            StockIdentity("000651", "格力电器", "CN"),
            StockIdentity("600690", "海尔智家", "CN"),
        ]

        mapping: Dict[str, StockIdentity] = {}
        for item in raw:
            mapping[self.normalize_code(item.code)] = item
        return mapping
