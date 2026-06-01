from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from urllib.request import Request, urlopen

from circle_predict.dashboard import get_dashboard_payload
from circle_predict.env import load_env


DIMENSION_LABELS = {
    "valuation": "估值",
    "sentiment": "情绪",
    "market": "市场",
    "macro": "宏观",
}


def format_score(value: float | int | None) -> str:
    if value is None:
        return "--"
    return f"{float(value):.0f}"


def format_dimensions(scores: dict) -> str:
    parts = []
    for key, label in DIMENSION_LABELS.items():
        if key in scores:
            parts.append(f"{label}{format_score(scores[key])}")
    return " / ".join(parts) if parts else "暂无"


def notable_indicators(indicators: list[dict], count: int = 2) -> str:
    valid = [item for item in indicators if item.get("score") is not None]
    if not valid:
        return "暂无可用指标"

    hot = sorted(valid, key=lambda item: item["score"], reverse=True)[:count]
    cold = sorted(valid, key=lambda item: item["score"])[:count]
    hot_text = "、".join(f"{item['name']} {format_score(item['score'])}" for item in hot)
    cold_text = "、".join(f"{item['name']} {format_score(item['score'])}" for item in cold)
    return f"偏热：{hot_text}；偏冷：{cold_text}"


def build_daily_report(payload: dict) -> str:
    generated_at = datetime.fromisoformat(payload["generated_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# CirclePredict 每日市场周期评分",
        f"> 生成时间：{generated_at}",
        "> 分数含义：高分 = 高估/狂热/拥挤程度更高",
        "",
    ]

    for market in payload.get("markets", []):
        label = market.get("label", market.get("market", "未知市场"))
        if market.get("available") is False:
            lines.extend([
                f"## {label}",
                "- 状态：数据不可用",
                f"- 原因：{market.get('unavailable_reason', '未知')}",
                "",
            ])
            continue

        stale_count = len(market.get("stale_indicators", []))
        stale_text = f"{stale_count} 个指标沿用旧值" if stale_count else "数据完整"
        lines.extend([
            f"## {label}",
            f"- 评分：短周期 {format_score(market.get('short_score'))} / 长周期 {format_score(market.get('long_score'))}",
            f"- 位置：{market.get('position_label', '未分区')}（{market.get('position_range_label', '--')}）",
            f"- 四维：{format_dimensions(market.get('dimension_scores', {}))}",
            f"- 指标：{notable_indicators(market.get('indicators', []))}",
            f"- 日期：{market.get('as_of_date', '--')}，{stale_text}",
            "",
        ])

    notes = [
        note for note in (payload.get("data_notes") or [])
        if "获取失败" in note or " 失败：" in note or "未返回有效" in note or "未成功返回" in note
    ]
    if notes:
        lines.extend([
            "## 数据异常",
            f"- {' '.join(notes)}",
            "",
        ])

    public_url = os.getenv("CIRCLEPREDICT_PUBLIC_URL", "").strip()
    if public_url:
        lines.extend([
            f"[查看仪表盘]({public_url})",
            "",
        ])

    return "\n".join(lines).strip()


def send_wecom_markdown(webhook_url: str, content: str, timeout: int = 10) -> dict:
    body = json.dumps(
        {"msgtype": "markdown", "markdown": {"content": content}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Send CirclePredict daily report to WeCom.")
    parser.add_argument("--dry-run", action="store_true", help="print report instead of sending")
    args = parser.parse_args()

    payload = get_dashboard_payload()
    report = build_daily_report(payload)
    if args.dry_run:
        print(report)
        return

    webhook_url = os.getenv("CIRCLEPREDICT_WECOM_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise SystemExit("CIRCLEPREDICT_WECOM_WEBHOOK_URL is not configured in .env")

    result = send_wecom_markdown(webhook_url, report)
    if result.get("errcode") != 0:
        raise SystemExit(f"WeCom webhook failed: {result}")
    print("Daily report sent to WeCom.")


if __name__ == "__main__":
    main()
