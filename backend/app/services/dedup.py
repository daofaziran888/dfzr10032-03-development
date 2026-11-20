"""采集去重业务规则：状态流转、字段校验与筛选口径都收在这里。

去重的输入不是原始页面，而是网页正文明细里「已确认」的那部分：
同一个来源网址、同一段正文（空白归一后比对指纹）只保留一条，其余记为重复，
并在去重结果里写明重复页对应的保留记录，方便回查确认过的正文。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from app.services.crawler import DETAIL_CONFIRMED, DETAIL_MODULE, body_fingerprint
from app.store import store

RETENTION = "保留首条确认记录"

MODULE = "dedup"
REQUIRED_FIELDS = ["策略编号", "输入来源", "去重键"]
STATUS_ORDER = ["草稿", "已配置", "生效中", "已停用"]
ACTION_RULES = {"提交配置": "已配置", "启用策略": "生效中", "停用策略": "已停用"}
NEGATIVE_ACTIONS = ["停用策略"]

RESULT_MODULE = "dedup_result"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _detail_sort_key(detail: dict[str, Any]) -> tuple[str, int, int]:
    try:
        page_no = int(detail.get("页码") or 0)
    except (TypeError, ValueError):
        page_no = 0
    return str(detail.get("规则编号") or ""), page_no, int(detail.get("id", 0))


class DedupService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("策略编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"去重策略 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集去重可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"去重策略已{action}"

    # ---------- 去重执行 ----------
    def _confirmed_details(self, rule_code: str | None, batch_code: str | None) -> list[dict[str, Any]]:
        details = [
            row for row in store.rows(DETAIL_MODULE)
            if row.get("状态") == DETAIL_CONFIRMED
        ]
        if rule_code:
            details = [row for row in details if str(row.get("规则编号")) == rule_code]
        if batch_code:
            details = [row for row in details if str(row.get("批次编号")) == batch_code]
        details.sort(key=_detail_sort_key)
        return details

    def run(self, entry_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"去重策略 {entry_id} 不存在或已归档"
        if entry.get("status") != "生效中":
            return None, f"去重策略 {entry.get('策略编号')} 尚未生效，先启用策略再执行去重"

        rule_code = str(values.get("规则编号") or "").strip() or None
        batch_code = str(values.get("批次编号") or "").strip() or None
        details = self._confirmed_details(rule_code, batch_code)
        if not details:
            scope = f"规则 {rule_code}" if rule_code else "全部规则"
            return None, f"{scope}没有已确认的网页正文明细，去重不会读取未确认的正文"

        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for detail in details:
            # 以正文现算指纹为准：明细里的指纹可能是旧规则/旧版本留下的
            fingerprint = body_fingerprint(str(detail.get("正文") or "")) or str(detail.get("正文指纹") or "")
            groups[(str(detail.get("来源网址") or ""), fingerprint)].append(detail)

        strategy = str(entry.get("策略编号") or "")
        keep_rule = str(entry.get("保留策略") or "") or RETENTION
        conflicts = self._retention_conflicts(groups)
        existing = {
            (str(row.get("策略编号")), str(row.get("重复明细编号")))
            for row in store.rows(RESULT_MODULE)
        }
        now = _now()
        results: list[dict[str, Any]] = []
        duplicates = 0
        recorded = 0
        for (source_url, fingerprint), members in groups.items():
            kept = members[0]
            for member in members[1:]:
                duplicates += 1
                key = (strategy, str(member.get("明细编号")))
                if key in existing:
                    recorded += 1
                    continue
                result = {
                    "id": max((int(row.get("id", 0)) for row in store.rows(RESULT_MODULE)), default=0) + 1,
                    "结果编号": self._next_result_code(),
                    "策略编号": strategy,
                    "规则编号": member.get("规则编号"),
                    "来源网址": source_url,
                    "页码": member.get("页码"),
                    "保留明细编号": kept.get("明细编号"),
                    "重复明细编号": member.get("明细编号"),
                    "保留来源网址": kept.get("来源网址"),
                    "正文指纹": fingerprint,
                    "结论": "重复",
                    "保留策略": keep_rule,
                    "处理时间": now,
                }
                store.rows(RESULT_MODULE).append(result)
                existing.add(key)
                results.append(result)

        message = (
            f"已按策略 {strategy} 比对 {len(details)} 条已确认正文，识别重复 {duplicates} 条"
        )
        if recorded:
            message += f"，其中 {recorded} 条此前已记录过保留关系"
        if conflicts:
            message += (
                f"；注意 {len(conflicts)} 个来源网址在去重前已被确认为多条不同正文，"
                "按保留策略需人工回查"
            )
        if duplicates == 0:
            message += "；没有新的重复页"
        elif results:
            message += f"；重复页的保留记录已写入去重结果（如保留明细 {results[0]['保留明细编号']}）"
        else:
            message += "；重复页的保留记录此前已全部写入，本次不重复写"
        return {
            "策略编号": strategy,
            "输入来源": entry.get("输入来源"),
            "去重键": entry.get("去重键"),
            "比对条数": len(details),
            "重复条数": duplicates,
            "保留策略": keep_rule,
            "保留冲突": conflicts,
            "新增重复": len(results),
            "已有重复": recorded,
            "结果": results,
        }, message

    def _retention_conflicts(
        self,
        groups: dict[tuple[str, str], list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """同一来源网址下存在多段不同正文时，说明该网址此前已被确认过多次，保留关系需要人工确认。"""
        bodies_by_url: dict[str, set[str]] = defaultdict(set)
        for (source_url, fingerprint) in groups:
            bodies_by_url[source_url].add(fingerprint)
        return [
            {"来源网址": source_url, "不同正文数": len(fingerprints)}
            for source_url, fingerprints in bodies_by_url.items()
            if len(fingerprints) > 1
        ]

    @staticmethod
    def _next_result_code() -> str:
        biggest = 0
        for row in store.rows(RESULT_MODULE):
            digits = "".join(char for char in str(row.get("结果编号") or "") if char.isdigit())
            if digits:
                biggest = max(biggest, int(digits))
        return f"DDR-{biggest + 1:04d}"

    def list_results(
        self,
        *,
        strategy_code: str | None = None,
        rule_code: str | None = None,
        source_url: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(RESULT_MODULE))
        if strategy_code:
            rows = [row for row in rows if str(row.get("策略编号")) == strategy_code]
        if rule_code:
            rows = [row for row in rows if str(row.get("规则编号")) == rule_code]
        if source_url:
            rows = [row for row in rows if source_url in str(row.get("来源网址", ""))]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    def preview_failure(self, entry_id: int) -> str:
        """预览前的状态检查：没生效的策略不能拿已确认正文做去重预演。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return f"去重策略 {entry_id} 不存在或已归档"
        if entry.get("status") != "生效中":
            return f"去重策略 {entry.get('策略编号')} 尚未生效，先启用策略再预演去重"
        return ""

    def preview(self, entry_id: int, values: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """预览去重口径：只读，不落结果，用来确认会比对哪些已确认正文。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        values = values or {}
        rule_code = str(values.get("规则编号") or "").strip() or None
        batch_code = str(values.get("批次编号") or "").strip() or None
        details = self._confirmed_details(rule_code, batch_code)
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for detail in details:
            fingerprint = body_fingerprint(str(detail.get("正文") or "")) or str(detail.get("正文指纹") or "")
            groups[(str(detail.get("来源网址") or ""), fingerprint)].append(detail)
        candidates = [
            {
                "来源网址": source_url,
                "正文指纹": fingerprint,
                "明细编号": [member.get("明细编号") for member in members],
            }
            for (source_url, fingerprint), members in groups.items()
            if len(members) > 1
        ]
        return {
            "策略编号": entry.get("策略编号"),
            "比对条数": len(details),
            "重复组数": len(candidates),
            "候选重复": candidates,
        }
