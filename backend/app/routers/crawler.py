"""网页采集规则接口：维护采集规则，并支持按规则从缓存样本抽取正文。

接口分三组：
- 规则：登记、筛选、状态流转（提交测试、发布规则、停用规则）；
- 页面样本：登记缓存样本、补录缓存；
- 抽取与确认：按规则抽正文、查看批次/页记录/正文明细、确认、恢复中断批次。
确认与恢复的闸门都在 app/services/crawler.py 里，路由层只负责搬运参数。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import settings
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.crawler import BATCH_STATUSES, DETAIL_STATUSES, CrawlerService

router = APIRouter(prefix="/api/crawler", tags=["网页采集规则"])

service = CrawlerService()

LIST_FIELDS = ["规则编号", "目标网址", "页面选择器", "正文选择器", "翻页参数", "抓取频率", "字符编码", "去重策略"]
STATUSES = ["草稿", "测试中", "已发布", "已停用"]


def _page_size(size: int) -> int:
    if size > settings.page_size_max:
        raise HTTPException(
            status_code=400,
            detail=f"每页最多 {settings.page_size_max} 条，请缩小分页范围",
        )
    return size


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出网页采集规则清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "crawler", "total": total, "items": items}


@router.get("/details/export")
def export_details(
    rule_code: str | None = Query(default=None),
    batch_code: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """导出网页正文明细：保留来源网址、页码和抽取所用的规则，供去重与回查使用。"""
    items, total = service.list_details(
        rule_code=rule_code, batch_code=batch_code, status=status, page=1, size=10000
    )
    return {"module": "crawler_detail", "total": total, "items": items}


@router.get("/samples", response_model=PageResult[dict])
def list_samples(
    rule_code: str | None = Query(default=None, description="按规则编号过滤"),
    cache_status: str | None = Query(default=None, description="就绪、缓存缺失"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """缓存页面样本列表；列表里不回传页面正文，避免整页 HTML 撑大响应。"""
    items, total = service.list_samples(rule_code=rule_code, cache_status=cache_status, page=page, size=_page_size(size))
    items = [{key: value for key, value in row.items() if key != "页面内容"} for row in items]
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/samples/{sample_id}", response_model=dict)
def get_sample(sample_id: int) -> dict:
    sample = service.get_sample(sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail=f"页面样本 {sample_id} 不存在或已归档")
    return sample


@router.post("/samples", response_model=ActionResult)
def save_sample(payload: EntryPayload) -> ActionResult:
    """登记一份缓存样本，或补录已有样本的缓存内容（补录后可用恢复抽取补缺页）。"""
    sample_id = payload.values.get("id")
    try:
        target_id = int(sample_id) if sample_id not in (None, "") else None
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="样本 id 必须是整数")
    sample, message = service.save_sample(payload.values, target_id)
    if sample is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=sample)


@router.get("/batches", response_model=PageResult[dict])
def list_batches(
    rule_code: str | None = Query(default=None, description="按规则编号过滤"),
    status: str | None = Query(default=None, description="、".join(BATCH_STATUSES)),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """采集批次列表：带上页记录与正文明细，方便直接查剩余页面去向。"""
    items, total = service.list_batches(rule_code=rule_code, status=status, page=page, size=_page_size(size))
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batches/{batch_id}", response_model=dict)
def get_batch(batch_id: int) -> dict:
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"采集批次 {batch_id} 不存在或已归档")
    return service.batch_view(batch)


@router.post("/batches/{batch_id}/actions", response_model=ActionResult)
def run_batch_action(batch_id: int, payload: EntryPayload) -> ActionResult:
    """批次动作：确认批次（半截批次会被拦下）、恢复抽取（只补未确认页）、作废批次。"""
    action = str(payload.values.get("action") or "").strip()
    batch, message = service.run_batch_action(batch_id, action, payload.values)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/details", response_model=PageResult[dict])
def list_details(
    rule_code: str | None = Query(default=None, description="按规则编号过滤"),
    batch_code: str | None = Query(default=None, description="按批次编号过滤"),
    source_url: str | None = Query(default=None, description="按来源网址检索"),
    status: str | None = Query(default=None, description="、".join(DETAIL_STATUSES)),
    keyword: str | None = Query(default=None, description="按正文或明细编号检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """网页正文明细数据集：每条都带来源网址、页码与抽取所用的规则，确认后可回查。"""
    items, total = service.list_details(
        rule_code=rule_code,
        batch_code=batch_code,
        source_url=source_url,
        status=status,
        keyword=keyword,
        page=page,
        size=_page_size(size),
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/details/{detail_id}", response_model=dict)
def get_detail(detail_id: int) -> dict:
    detail = service.get_detail(detail_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"网页正文明细 {detail_id} 不存在或已归档")
    return detail


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、测试中、已发布、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤网页采集规则列表；没有数据时返回空页，不报错。"""
    _page_size(size)
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条采集规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采集规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条采集规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="采集规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条采集规则执行提交测试、发布规则、停用规则；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/extract", response_model=ActionResult)
def extract_pages(entry_id: int, payload: EntryPayload) -> ActionResult:
    """按规则从缓存样本抽取正文，落成一批待确认的网页正文明细。"""
    batch, message = service.extract(entry_id, payload.values)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)
