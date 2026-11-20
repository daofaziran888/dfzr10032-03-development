"""网页采集正文明细与去重的回归测试。

覆盖：按规则抽取正文、缺页时半截批次不许确认、恢复只补未确认页、
规则停用与恢复的冲突、以及去重只读已确认正文并记录保留关系。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 允许直接 `python3 -m pytest backend/tests` 从仓库根目录跑
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.store import Store, store  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    # 每个用例重置内存仓库，避免用例之间互相污染
    fresh = Store()
    for name, rows in fresh._tables.items():
        store._tables[name] = [dict(row) for row in rows]
    return TestClient(app)


def values(**kwargs) -> dict:
    return {"values": kwargs}


def run(client: TestClient, batch_id: int, action: str, **kwargs) -> dict:
    return client.post(f"/api/crawler/batches/{batch_id}/actions", json=values(action=action, **kwargs)).json()


def test_extract_keeps_source_page_and_rule(client: TestClient) -> None:
    entry = client.post("/api/crawler/2/extract", json=values()).json()
    assert entry["ok"], entry["message"]

    details = client.get("/api/crawler/details?rule_code=CRAW-0002").json()["items"]
    assert {detail["页码"] for detail in details} == {1, 2}
    for detail in details:
        assert detail["来源网址"].startswith("https://blog.example.com/")
        assert detail["规则编号"] == "CRAW-0002"
        assert detail["正文选择器"] == "div.post div.body"
        assert detail["正文"]


def test_incomplete_batch_cannot_be_confirmed(client: TestClient) -> None:
    # BAT-0001 的第 2 页缓存缺失
    result = run(client, 1, "确认批次")
    assert result["ok"] is False
    assert "缺页" in result["message"]

    batch = client.get("/api/crawler/batches/1").json()
    assert batch["状态"] == "待确认"
    assert batch["可确认"] is False
    # 半截抽取结果只以待确认明细留存，没有被带进已确认
    assert all(detail["状态"] == "待确认" for detail in batch["明细"])


def test_resume_after_cache_filled_confirms(client: TestClient) -> None:
    payload = values(id=2, 页面内容='<article class="item"><div class="content">补录的第二页正文。</div></article>')
    assert client.post("/api/crawler/samples", json=payload).json()["ok"]

    resumed = run(client, 1, "恢复抽取")
    assert resumed["ok"]
    assert "补齐 1 页" in resumed["message"]
    assert "跳过已有明细 2 页" in resumed["message"]

    confirmed = run(client, 1, "确认批次")
    assert confirmed["ok"], confirmed["message"]
    assert confirmed["entry"]["已确认页数"] == 3


def test_resume_does_not_redeliver_confirmed_pages(client: TestClient) -> None:
    before = client.get("/api/crawler/details?batch_code=BAT-0002").json()["total"]
    result = run(client, 2, "恢复抽取")
    assert result["ok"]
    assert "不会重复交付" in result["message"]
    after = client.get("/api/crawler/details?batch_code=BAT-0002").json()["total"]
    assert before == after == 2


def test_resume_after_disable_releases_unstarted_pages(client: TestClient) -> None:
    # 先停用规则，BAT-0003 里未开始的第 2、3 页应被释放
    stop = client.post("/api/crawler/3/actions", json=values(action="停用规则")).json()
    assert stop["ok"]
    assert "已释放 2 页" in stop["message"]

    resume = run(client, 3, "恢复抽取")
    assert resume["ok"] is False
    assert "已停用" in resume["message"]

    batch = client.get("/api/crawler/batches/3").json()
    assert batch["状态"] == "已终止"
    pages = {page["页码"]: page["状态"] for page in batch["页记录"]}
    assert pages[1] == "已抽取"
    assert pages[2] == pages[3] == "已释放"
    assert "批次已终止" in batch["阻断原因"]

    # 停用后也不能再领取新页面，但已确认/已抽取的明细仍可回查
    blocked = client.post("/api/crawler/3/extract", json=values()).json()
    assert blocked["ok"] is False
    assert "已停用" in blocked["message"]
    assert client.get("/api/crawler/details?batch_code=BAT-0003").json()["total"] == 1


def test_dedup_reads_only_confirmed_details(client: TestClient) -> None:
    # BAT-0001 未确认，去重不应把它的正文算进来
    preview = client.post("/api/dedup/3/preview", json=values()).json()
    assert preview["ok"]
    assert preview["entry"]["比对条数"] == 2

    result = client.post("/api/dedup/3/run", json=values()).json()
    assert result["ok"], result["message"]
    assert result["entry"]["比对条数"] == 2


def test_dedup_records_retained_record(client: TestClient) -> None:
    detail = client.post("/api/crawler/samples", json=values(
        id=5,
        页面内容=(
            '<div class="post"><div class="body">采集队列需要背压：当写入端变慢时，'
            '抓取端必须限速，否则内存会被未落盘的结果撑爆。</div></div>'
        ),
    )).json()
    assert detail["ok"]

    # 再抽一次 CRAW-0002 并确认，让同一来源网址出现重复正文
    client.post("/api/crawler/2/extract", json=values())
    batches = client.get("/api/crawler/batches?rule_code=CRAW-0002").json()["items"]
    new_batch = next(batch for batch in batches if batch["状态"] != "已确认")
    assert run(client, new_batch["id"], "确认批次")["ok"]

    result = client.post("/api/dedup/3/run", json=values()).json()
    assert result["ok"]
    duplicates = result["entry"]["结果"]
    assert duplicates
    for row in duplicates:
        assert row["保留明细编号"] != row["重复明细编号"]
        kept = client.get(f"/api/crawler/details?keyword={row['保留明细编号']}").json()["items"]
        assert kept and kept[0]["状态"] == "已确认"

    # 重复执行不会重复写结果
    again = client.post("/api/dedup/3/run", json=values()).json()
    assert again["ok"]
    assert again["entry"]["新增重复"] == 0


def test_dedup_requires_active_strategy(client: TestClient) -> None:
    result = client.post("/api/dedup/1/run", json=values()).json()
    assert result["ok"] is False
    assert "尚未生效" in result["message"]
