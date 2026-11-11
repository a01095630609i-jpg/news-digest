"""RSS/Atom 피드에서 관심 주제 글만 골라 digest/YYYY-MM-DD.md 로 요약한다.

사용법:
    python rss_digest.py

- 피드에 실린 제목과 요약(content/summary/description)만 사용한다. 기사 본문 페이지는 열지 않는다.
- ANTHROPIC_API_KEY(또는 `ant auth login` 프로필)와 anthropic 패키지가 있으면
  Claude 로 요약을 "내 말로" 다시 쓴다. 없으면 규칙 기반 축약 요약으로 대신한다.
- 결과 파일은 이 스크립트가 있는 폴더 아래 digest/ 에만 쓴다(같은 날짜 파일은 덮어쓴다).
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────
FEED_URL = "https://news.hada.io/rss/news"
MAX_ITEMS = 5
MODEL = "claude-opus-5-5"

# 관심 주제 → 매칭 키워드(소문자 비교)
TOPICS: dict[str, list[str]] = {
    "AI 에이전트": ["에이전트", "agent", "agentic", "mcp"],
    "업무 자동화": ["자동화", "automation", "automate", "워크플로", "workflow", "rpa"],
    "클로드": ["클로드", "claude", "anthropic", "앤트로픽"],
}

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "digest"

ATOM = "{http://www.w3.org/2005/Atom}"


# ── 피드 읽기 ─────────────────────────────────────────
def fetch_feed(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (rss-digest)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def strip_utm(url: str) -> str:
    url = re.sub(r"[?&]utm_[^=&]+=[^&]*", "", url)
    return url.replace("&", "?", 1) if "?" not in url else url


def parse_feed(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    items: list[dict] = []
    if root.tag == f"{ATOM}feed":  # Atom
        for e in root.findall(f"{ATOM}entry"):
            link = ""
            for l in e.findall(f"{ATOM}link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            body = e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content") or ""
            items.append({
                "title": clean_text(e.findtext(f"{ATOM}title") or ""),
                "summary": clean_text(body),
                "link": strip_utm(link),
            })
    else:  # RSS 2.0
        for it in root.iter("item"):
            items.append({
                "title": clean_text(it.findtext("title") or ""),
                "summary": clean_text(it.findtext("description") or ""),
                "link": strip_utm((it.findtext("link") or "").strip()),
            })
    return items


# ── 관심 주제 필터 ─────────────────────────────────────
def score(item: dict) -> tuple[int, list[str]]:
    title = item["title"].lower()
    summary = item["summary"].lower()
    total, matched = 0, []
    for topic, keywords in TOPICS.items():
        s = 0
        for kw in keywords:
            if re.search(rf"(?<![a-z]){re.escape(kw)}", title):
                s += 3  # 제목에 있으면 가중치
            if re.search(rf"(?<![a-z]){re.escape(kw)}", summary):
                s += 1
        if s:
            matched.append(topic)
            total += s
    return total, matched


def pick(items: list[dict]) -> list[dict]:
    scored = []
    for idx, it in enumerate(items):
        s, topics = score(it)
        if s >= 2:  # 제목 매칭 또는 요약에서 2회 이상 → 본문에 한 번 스친 단어는 제외
            scored.append((s, -idx, {**it, "topics": topics}))
    scored.sort(reverse=True)
    return [x[2] for x in scored[:MAX_ITEMS]]


# ── 요약 (Claude 사용, 실패 시 규칙 기반) ─────────────────
def summarize_with_claude(articles: list[dict]) -> dict | None:
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        payload = [
            {"id": i, "title": a["title"], "feed_summary": a["summary"][:1500]}
            for i, a in enumerate(articles)
        ]
        prompt = (
            "아래는 RSS 피드에 실린 기사 제목과 요약이다. 이 정보만 사용해서 작업해라.\n"
            "1) 기사마다 한국어 두 문장 요약을 쓴다. 원문 문장을 길게 그대로 옮기지 말고 네 말로 다시 쓴다.\n"
            "2) 전체를 아우르는 '오늘의 세 줄 요약'을 세 줄로 쓴다.\n"
            "JSON 하나만 출력한다. 형식: "
            '{"three_lines": ["...", "...", "..."], "items": [{"id": 0, "summary": "..."}]}\n\n'
            + json.dumps(payload, ensure_ascii=False)
        )
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in resp.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:  # 인증 없음, 네트워크 오류 등 → 규칙 기반으로
        print(f"[알림] Claude 요약을 건너뜀: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def shorten(sentence: str, limit: int = 90) -> str:
    sentence = sentence.strip()
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def rule_summary(item: dict) -> str:
    sents = [s for s in re.split(r"(?<=[.!?다요])\s+", item["summary"]) if len(s) > 8]
    if not sents:
        return f"{shorten(item['title'])}에 관한 글입니다. 피드에 별도 요약이 없습니다."
    first = shorten(sents[0])
    second = shorten(sents[1]) if len(sents) > 1 else f"주제: {', '.join(item['topics'])}."
    return f"{first} {second}"


def rule_three_lines(articles: list[dict]) -> list[str]:
    lines = [f"{shorten(a['title'], 60)} ({', '.join(a['topics'])})" for a in articles[:3]]
    while len(lines) < 3:
        lines.append(f"관심 주제 관련 글은 총 {len(articles)}건입니다.")
    return lines


# ── 파일 쓰기 ──────────────────────────────────────────
def build_markdown(today: str, articles: list[dict]) -> str:
    if not articles:
        return "해당 없음\n"

    result = summarize_with_claude(articles)
    if result:
        three = list(result.get("three_lines", []))[:3]
        sums = {it.get("id"): it.get("summary", "") for it in result.get("items", [])}
    else:
        three, sums = [], {}
    if len(three) < 3:
        three = rule_three_lines(articles)

    out = [f"# {today} 뉴스 다이제스트", "", "## 오늘의 세 줄 요약", ""]
    out += [f"{i}. {line}" for i, line in enumerate(three, 1)]
    out.append("")
    for i, a in enumerate(articles):
        out += [
            f"## {i + 1}. {a['title']}",
            "",
            sums.get(i) or rule_summary(a),
            "",
            f"원문: {a['link']}",
            "",
        ]
    return "\n".join(out)


def main() -> None:
    today = date.today().isoformat()
    items = parse_feed(fetch_feed(FEED_URL))
    articles = pick(items)

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{today}.md"
    path.write_text(build_markdown(today, articles), encoding="utf-8")  # 덮어쓰기

    print(f"저장: {path} / 고른 기사 {len(articles)}건")


if __name__ == "__main__":
    main()
