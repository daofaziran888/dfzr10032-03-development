"""网页采集规则业务规则：状态流转、字段校验与筛选口径都收在这里。

除了规则本身，还负责按规则把缓存页面样本解析成网页正文明细：
- 一次抽取落一个批次，批次里的每一页都记来源网址、页码和抽取所用的规则；
- 缺页或抽取失败的批次不许确认，半截正文只以「待确认」明细留存；
- 恢复抽取只补未确认的页，已经确认的页不会重复交付；
- 规则停用时释放尚未领取的页面，剩余页面的去向能从批次和页记录里查到。
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any

from app.services.extract import extract_body, normalize_text
from app.store import store

MODULE = "crawler"
REQUIRED_FIELDS = ["规则编号", "目标网址", "页面选择器"]
STATUS_ORDER = ["草稿", "测试中", "已发布", "已停用"]
ACTION_RULES = {"提交测试": "测试中", "发布规则": "已发布", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]

SAMPLE_MODULE = "crawler_sample"
BATCH_MODULE = "crawler_batch"
PAGE_MODULE = "crawler_page"
DETAIL_MODULE = "crawler_detail"

BATCH_PENDING = "待确认"
BATCH_CONFIRMED = "已确认"
BATCH_INTERRUPTED = "已中断"
BATCH_TERMINATED = "已终止"
BATCH_STATUSES = [BATCH_PENDING, BATCH_CONFIRMED, BATCH_INTERRUPTED, BATCH_TERMINATED]

PAGE_WAITING = "待抽取"
PAGE_EXTRACTED = "已抽取"
PAGE_FAILED = "抽取失败"
PAGE_MISSING = "缺页"
PAGE_CONFIRMED = "已确认"
PAGE_RELEASED = "已释放"

DETAIL_PENDING = "待确认"
DETAIL_CONFIRMED = "已确认"
DETAIL_STATUSES = [DETAIL_PENDING, DETAIL_CONFIRMED]

CONFIRM_BLOCKING_PAGES = {PAGE_WAITING, PAGE_FAILED, PAGE_MISSING}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(module: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(module)), default=0) + 1


def _next_code(module: str, code_field: str, prefix: str, width: int = 4) -> str:
    """按已有编号里的数字续号，保证批次号、明细号不会撞车。"""
    biggest = 0
    for row in store.rows(module):
        raw = str(row.get(code_field) or "")
        digits = "".join(char for char in raw if char.isdigit())
        if digits:
            biggest = max(biggest, int(digits))
    return f"{prefix}-{biggest + 1:0{width}d}"


def body_fingerprint(body: str) -> str:
    return sha256(normalize_text(body).encode("utf-8")).hexdigest()[:16]


def _page_sort_key(page: dict[str, Any]) -> tuple[int, int]:
    try:
        page_no = int(page.get("页码") or 0)
    except (TypeError, ValueError):
        page_no = 0
    return page_no, int(page.get("id", 0))


class CrawlerService:
    # ---------- 规则 ----------
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
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
            return None, f"采集规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于网页采集规则可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        suffix = ""
        if target == "已停用":
            released = self.release_unstarted(str(entry.get("规则编号") or ""), "规则已停用，抽取中止，本页不再领取")
            if released:
                suffix = f"；已释放 {len(released)} 页未开始页面，可在采集批次中查看去向"
            else:
                suffix = "；本批没有未开始的页面，已抽取未确认的页仍留在批次里可回查"
        return entry, f"采集规则已{action}{suffix}"

    # ---------- 页面样本 ----------
    def list_samples(
        self,
        *,
        rule_code: str | None = None,
        cache_status: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(SAMPLE_MODULE))
        if rule_code:
            rows = [row for row in rows if str(row.get("规则编号")) == rule_code]
        if cache_status:
            rows = [row for row in rows if row.get("缓存状态") == cache_status]
        rows.sort(key=lambda row: (str(row.get("规则编号")), _page_sort_key(row)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_sample(self, sample_id: int) -> dict[str, Any] | None:
        return store.find(SAMPLE_MODULE, sample_id)

    def save_sample(self, values: dict[str, Any], sample_id: int | None = None) -> tuple[dict[str, Any] | None, str]:
        """登记或补录缓存样本。补录缓存后可以用「恢复抽取」把缺页补回来。"""
        existing = store.find(SAMPLE_MODULE, sample_id) if sample_id is not None else None
        if sample_id is not None and existing is None:
            return None, f"页面样本 {sample_id} 不存在或已归档"
        rule_code = str(values.get("规则编号") or (existing or {}).get("规则编号") or "").strip()
        if not rule_code:
            return None, "缺少必填字段：规则编号"
        if not any(str(row.get("规则编号")) == rule_code for row in store.rows(MODULE)):
            return None, f"规则编号「{rule_code}」尚未登记，先维护采集规则"
        try:
            page_no = int(values.get("页码") or (existing or {}).get("页码") or 0)
        except (TypeError, ValueError):
            return None, "页码必须是整数"
        if page_no <= 0:
            return None, "页码必须是正整数"
        source_url = str(values.get("来源网址") or (existing or {}).get("来源网址") or "").strip()
        if not source_url:
            return None, "缺少必填字段：来源网址"
        if existing is None:
            rows = store.rows(SAMPLE_MODULE)
            sample = {                "id": _next_id(SAMPLE_MODULE),
                "样本编号": _next_code(SAMPLE_MODULE, "样本编号", "SMP"),
                "规则编号": rule_code,
                "来源网址": source_url,
                "页码": page_no,
                "缓存状态": str(values.get("缓存状态") or "就绪"),
                "缓存时间": str(values.get("缓存时间") or (_now() if str(values.get("页面内容") or "").strip() else "")),
                "页面内容": str(values.get("页面内容") or ""),
            }
            rows.append(sample)
            return sample, "页面样本已登记"
        sample = existing
        if sample is None:
            return None, f"页面样本 {sample_id} 不存在或已归档"
        sample["缓存状态"] = str(values.get("缓存状态") or "就绪")
        sample["缓存时间"] = str(
            values.get("缓存时间") or (_now() if str(values.get("页面内容") or "").strip() else sample.get("缓存时间") or "")
        )
        if "页面内容" in values:
            sample["页面内容"] = str(values.get("页面内容") or "")
        if "来源网址" in values:
            sample["来源网址"] = source_url
        sample["页码"] = page_no
        return sample, "页面样本已更新"

    # ---------- 按规则抽取正文 ----------
    def extract(self, entry_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采集规则 {entry_id} 不存在或已归档"
        if entry.get("status") == "已停用":
            return None, "采集规则已停用，不能再领取新页面"
        rule_code = str(entry.get("规则编号") or "")
        samples = [row for row in store.rows(SAMPLE_MODULE) if str(row.get("规则编号")) == rule_code]
        wanted = values.get("样本编号")
        if isinstance(wanted, list) and wanted:
            wanted_set = {str(item) for item in wanted}
            samples = [row for row in samples if str(row.get("样本编号")) in wanted_set]
        if not samples:
            return None, f"规则 {rule_code} 下没有可抽取的页面样本"

        samples.sort(key=_page_sort_key)
        batch_id = _next_id(BATCH_MODULE)
        batch = {
            "id": batch_id,
            "批次编号": _next_code(BATCH_MODULE, "批次编号", "BAT"),
            "规则编号": rule_code,
            "页面总数": len(samples),
            "已确认页数": 0,
            "状态": BATCH_PENDING,
            "创建时间": _now(),
            "确认时间": "",
            "说明": "",
        }
        store.rows(BATCH_MODULE).append(batch)
        for sample in samples:
            page_row = self._new_page(batch, sample)
            self._extract_page(entry, sample, batch, page_row)
        self._refresh_batch(batch, "首次抽取完成")
        return self.batch_view(batch), f"已按规则 {rule_code} 抽取 {len(samples)} 页，等待确认"

    def _new_page(self, batch: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        page_row = {
            "id": _next_id(PAGE_MODULE),
            "批次编号": batch["批次编号"],
            "样本编号": sample.get("样本编号"),
            "规则编号": sample.get("规则编号"),
            "来源网址": sample.get("来源网址"),
            "页码": sample.get("页码"),
            "状态": PAGE_WAITING,
            "明细编号": "",
            "说明": "",
        }
        store.rows(PAGE_MODULE).append(page_row)
        return page_row

    def _extract_page(
        self,
        entry: dict[str, Any],
        sample: dict[str, Any],
        batch: dict[str, Any],
        page_row: dict[str, Any],
    ) -> bool:
        """抽取一页；已有明细（含待确认）的页不重复落明细。"""
        if page_row.get("明细编号"):
            return True
        if page_row.get("状态") == PAGE_CONFIRMED:
            return True
        if sample.get("缓存状态") != "就绪":
            page_row["状态"] = PAGE_MISSING
            page_row["说明"] = "缓存样本缺失，等待重新缓存后恢复抽取"
            return False
        body, reason = extract_body(str(sample.get("页面内容") or ""), entry.get("正文选择器"))
        if body is None:
            page_row["状态"] = PAGE_FAILED
            page_row["说明"] = reason or "正文抽取失败"
            return False
        detail = {
            "id": _next_id(DETAIL_MODULE),
            "明细编号": _next_code(DETAIL_MODULE, "明细编号", "DET"),
            "批次编号": batch["批次编号"],
            "样本编号": sample.get("样本编号"),
            "规则编号": entry.get("规则编号"),
            "来源网址": sample.get("来源网址"),
            "页码": sample.get("页码"),
            "正文": body,
            "正文指纹": body_fingerprint(body),
            "正文选择器": entry.get("正文选择器") or "",
            "状态": DETAIL_PENDING,
            "抽取时间": _now(),
            "确认时间": "",
        }
        store.rows(DETAIL_MODULE).append(detail)
        page_row["状态"] = PAGE_EXTRACTED
        page_row["明细编号"] = detail["明细编号"]
        page_row["说明"] = ""
        return True

    # ---------- 批次与明细 ----------
    def list_batches(
        self,
        *,
        rule_code: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(BATCH_MODULE))
        if rule_code:
            rows = [row for row in rows if str(row.get("规则编号")) == rule_code]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self.batch_view(row) for row in rows[start:start + size]], total

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(BATCH_MODULE, batch_id)

    def pages_of(self, batch_code: str) -> list[dict[str, Any]]:
        pages = [row for row in store.rows(PAGE_MODULE) if row.get("批次编号") == batch_code]
        pages.sort(key=_page_sort_key)
        return pages

    def details_of(self, batch_code: str) -> list[dict[str, Any]]:
        details = [row for row in store.rows(DETAIL_MODULE) if row.get("批次编号") == batch_code]
        details.sort(key=lambda row: _page_sort_key(row))
        return details

    def batch_view(self, batch: dict[str, Any]) -> dict[str, Any]:
        view = dict(batch)
        pages = self.pages_of(str(batch.get("批次编号") or ""))
        details = self.details_of(str(batch.get("批次编号") or ""))
        extracted_codes = {
            str(page.get("样本编号")) for page in pages
            if page.get("状态") in (PAGE_EXTRACTED, PAGE_CONFIRMED)
        }
        detail_codes = {str(detail.get("样本编号")) for detail in details}
        # 页记录说抽过、明细却找不到，说明中途丢过结果，不能算完整
        missing_codes = sorted(extracted_codes - detail_codes)
        view["页记录"] = pages
        view["明细"] = details
        view["已抽取页数"] = len(extracted_codes)
        view["已确认页数"] = sum(1 for page in pages if page.get("状态") == PAGE_CONFIRMED)
        view["缺明细样本"] = missing_codes
        blockers = self.confirm_blockers(batch)
        view["待处理页数"] = sum(1 for page in pages if page.get("状态") in CONFIRM_BLOCKING_PAGES)
        view["阻断原因"] = blockers
        view["可确认"] = not blockers and batch.get("状态") != BATCH_CONFIRMED
        return view

    def confirm_blockers(self, batch: dict[str, Any]) -> list[str]:
        """确认前必须没有未抽取、缺页或抽取失败的页，杜绝半截批次进已确认。"""
        if batch.get("状态") == BATCH_CONFIRMED:
            return []
        pages = self.pages_of(str(batch.get("批次编号") or ""))
        details = self.details_of(str(batch.get("批次编号") or ""))
        blockers: list[str] = []
        waiting = [page for page in pages if page.get("状态") == PAGE_WAITING]
        missing = [page for page in pages if page.get("状态") == PAGE_MISSING]
        failed = [page for page in pages if page.get("状态") == PAGE_FAILED]
        released = [page for page in pages if page.get("状态") == PAGE_RELEASED]
        if batch.get("状态") == BATCH_TERMINATED:
            blockers.append("批次已终止")
        if waiting:
            blockers.append(f"{len(waiting)} 页尚未抽取")
        if missing:
            blockers.append(f"{len(missing)} 页缓存缺页")
        if failed:
            blockers.append(f"{len(failed)} 页抽取失败")
        if released:
            blockers.append(f"{len(released)} 页已因规则停用释放")
        extracted_codes = {
            str(page.get("样本编号")) for page in pages
            if page.get("状态") in (PAGE_EXTRACTED, PAGE_CONFIRMED)
        }
        detail_codes = {str(detail.get("样本编号")) for detail in details}
        if extracted_codes - detail_codes:
            blockers.append("页记录与正文明细数量不一致")
        return blockers

    def confirm_batch(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        batch = store.find(BATCH_MODULE, batch_id)
        if batch is None:
            return None, f"采集批次 {batch_id} 不存在或已归档"
        if batch.get("状态") == BATCH_CONFIRMED:
            return self.batch_view(batch), "批次已确认，无需重复确认"
        blockers = self.confirm_blockers(batch)
        if blockers:
            return None, f"批次 {batch.get('批次编号')} 不能确认：{'；'.join(blockers)}。半截抽取结果只保留待确认明细"
        batch_code = str(batch.get("批次编号") or "")
        now = _now()
        pages = self.pages_of(batch_code)
        for detail in self.details_of(batch_code):
            detail["状态"] = DETAIL_CONFIRMED
            detail["确认时间"] = now
        for page in pages:
            if page.get("状态") in (PAGE_EXTRACTED, PAGE_CONFIRMED):
                page["状态"] = PAGE_CONFIRMED
        batch["状态"] = BATCH_CONFIRMED
        batch["确认时间"] = now
        batch["已确认页数"] = sum(1 for page in pages if page.get("状态") == PAGE_CONFIRMED)
        batch["说明"] = f"{batch['已确认页数']} 页正文已确认，可参与采集去重"
        return self.batch_view(batch), f"批次 {batch_code} 已确认 {batch['已确认页数']} 页正文"

    def resume_batch(
        self, batch_id: int, sample_codes: list[str] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """恢复中断批次：只补未确认页，已确认页原样保留、不重复交付。"""
        batch = store.find(BATCH_MODULE, batch_id)
        if batch is None:
            return None, f"采集批次 {batch_id} 不存在或已归档"
        batch_code = str(batch.get("批次编号") or "")
        if batch.get("状态") == BATCH_CONFIRMED:
            return self.batch_view(batch), f"批次 {batch_code} 已确认，恢复不会重复交付已确认的页"

        rule_code = str(batch.get("规则编号") or "")
        rule = next((row for row in store.rows(MODULE) if str(row.get("规则编号")) == rule_code), None)
        if rule is None:
            return None, f"批次 {batch_code} 关联的规则 {rule_code} 不存在，无法恢复"
        if rule.get("status") == "已停用":
            released = self.release_batch_pages(
                batch, "规则已停用，恢复抽取中止，本页不再领取"
            )
            suffix = f"已释放 {len(released)} 页未开始页面" if released else "没有未开始的页面需要释放"
            return None, (
                f"规则 {rule_code} 已停用，拒绝恢复批次 {batch_code}；{suffix}，"
                "本批已抽取但未确认的页仍留在批次里可回查，去向见页记录"
            )

        pages = self.pages_of(batch_code)
        wanted = {str(code) for code in sample_codes} if sample_codes else None
        added = 0
        skipped_confirmed = 0
        skipped_released = 0
        skipped_duplicate = 0
        attempted = 0
        for page_row in pages:
            sample_code = str(page_row.get("样本编号") or "")
            if wanted is not None and sample_code not in wanted:
                continue
            if page_row.get("状态") == PAGE_CONFIRMED:
                skipped_confirmed += 1
                continue
            if page_row.get("状态") == PAGE_RELEASED:
                skipped_released += 1
                continue
            if page_row.get("明细编号"):
                # 已有明细（待确认）的页不重复抽取，避免同一页交付两份正文
                skipped_duplicate += 1
                if page_row.get("状态") != PAGE_EXTRACTED:
                    page_row["状态"] = PAGE_EXTRACTED
                continue
            sample = next(
                (row for row in store.rows(SAMPLE_MODULE) if str(row.get("样本编号")) == sample_code),
                None,
            )
            attempted += 1
            if sample is None:
                page_row["状态"] = PAGE_MISSING
                page_row["说明"] = "缓存样本不存在，无法恢复抽取"
                continue
            if self._extract_page(rule, sample, batch, page_row):
                added += 1

        note = f"恢复抽取：重试 {attempted} 页，补齐 {added} 页"
        if skipped_confirmed:
            note += f"，跳过已确认 {skipped_confirmed} 页"
        if skipped_released:
            note += f"，跳过已释放 {skipped_released} 页"
        if skipped_duplicate:
            note += f"，跳过已有明细 {skipped_duplicate} 页"
        self._refresh_batch(batch, note)
        blockers = self.confirm_blockers(batch)
        if blockers:
            return self.batch_view(batch), f"{note}；仍有缺口：{'；'.join(blockers)}"
        return self.batch_view(batch), f"{note}；批次可以确认"

    def _refresh_batch(self, batch: dict[str, Any], note: str) -> None:
        pages = self.pages_of(str(batch.get("批次编号") or ""))
        batch["已确认页数"] = sum(1 for page in pages if page.get("状态") == PAGE_CONFIRMED)
        if batch.get("状态") == BATCH_CONFIRMED:
            return
        batch["状态"] = (
            BATCH_TERMINATED
            if any(page.get("状态") == PAGE_RELEASED for page in pages)
            else BATCH_PENDING
        )
        blockers = self.confirm_blockers(batch)
        batch["说明"] = note if not blockers else f"{note}；待处理：{'；'.join(blockers)}"

    def release_batch_pages(self, batch: dict[str, Any], reason: str) -> list[dict[str, Any]]:
        """把批次里还没开始抽取的页标记为已释放，让剩余页面的去向可查。"""
        released: list[dict[str, Any]] = []
        for page in self.pages_of(str(batch.get("批次编号") or "")):
            if page.get("状态") == PAGE_WAITING:
                page["状态"] = PAGE_RELEASED
                page["说明"] = reason
                released.append(page)
        if released:
            batch["状态"] = BATCH_TERMINATED
            batch["说明"] = f"{reason}；已释放 {len(released)} 页"
        return released

    def release_unstarted(self, rule_code: str, reason: str) -> list[dict[str, Any]]:
        """规则停用时，把所有关联批次里未开始的页面释放掉。已确认页与已抽取明细保持原样。"""
        released: list[dict[str, Any]] = []
        for batch in store.rows(BATCH_MODULE):
            if str(batch.get("规则编号")) != rule_code:
                continue
            if batch.get("状态") == BATCH_CONFIRMED:
                continue
            released.extend(self.release_batch_pages(batch, reason))
        return released

    def run_batch_action(self, batch_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        if action == "确认批次":
            return self.confirm_batch(batch_id)
        if action in ("恢复抽取", "重试中断页"):
            raw = values.get("样本编号")
            sample_codes = [str(item) for item in raw] if isinstance(raw, list) else None
            return self.resume_batch(batch_id, sample_codes)
        if action == "作废批次":
            batch = store.find(BATCH_MODULE, batch_id)
            if batch is None:
                return None, f"采集批次 {batch_id} 不存在或已归档"
            if batch.get("状态") == BATCH_CONFIRMED:
                return None, f"批次 {batch.get('批次编号')} 已确认，不能作废"
            released = self.release_batch_pages(batch, "批次已手工作废，本页不再领取")
            batch["状态"] = BATCH_TERMINATED
            batch["说明"] = f"批次已作废，释放 {len(released)} 页"
            return self.batch_view(batch), "批次已作废，未确认页面已释放"
        return None, f"动作「{action}」不属于采集批次可执行范围"

    def list_details(
        self,
        *,
        rule_code: str | None = None,
        batch_code: str | None = None,
        source_url: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(DETAIL_MODULE))
        if rule_code:
            rows = [row for row in rows if str(row.get("规则编号")) == rule_code]
        if batch_code:
            rows = [row for row in rows if str(row.get("批次编号")) == batch_code]
        if source_url:
            rows = [row for row in rows if source_url in str(row.get("来源网址", ""))]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("正文", "")) or keyword in str(row.get("明细编号", ""))
            ]
        rows.sort(key=lambda row: (str(row.get("规则编号")), _page_sort_key(row), int(row.get("id", 0))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    def get_detail(self, detail_id: int) -> dict[str, Any] | None:
        return store.find(DETAIL_MODULE, detail_id)
