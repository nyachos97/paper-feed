#!/usr/bin/env python3
"""논문 후보 수집기 (GitHub Actions에서 매일 새벽 실행).

OpenAlex를 1순위로, 막히면 Crossref로 관심 주제의 신규 논문을 모아
data/candidates/YYYY-MM-DD.json 에 저장합니다. 표준 라이브러리만 씁니다.

환경 변수(선택):
  CONTACT_EMAIL     OpenAlex/Crossref/Unpaywall 'polite pool'용 연락 이메일 (GitHub Secret 권장)
  OPENALEX_API_KEY  OpenAlex API 키가 있으면 사용
  FEED_TODAY        테스트용 기준 날짜(YYYY-MM-DD, KST)
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAND_DIR = DATA / "candidates"
STATE_FILE = DATA / "state.json"
INDEX_FILE = DATA / "index.json"
KST = dt.timezone(dt.timedelta(hours=9))

EMAIL = os.environ.get("CONTACT_EMAIL", "").strip()
OPENALEX_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()
UA = "paper-feed-collector/1.0 (+https://github.com; personal research newsfeed)" + (f" mailto:{EMAIL}" if EMAIL else "")

status: dict[str, dict] = {"openalex": {"ok": 0, "fail": 0}, "crossref": {"ok": 0, "fail": 0}, "unpaywall": {"ok": 0, "fail": 0}}


# ---------------------------------------------------------------- HTTP
def get_json(url: str, source: str, tries: int = 4) -> dict | None:
    delay = 5
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.loads(r.read().decode("utf-8"))
            status[source]["ok"] += 1
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                status[source]["ok"] += 1
                return None
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = int(retry_after) if (retry_after and retry_after.isdigit()) else delay
            print(f"  [{source}] HTTP {e.code} (시도 {attempt + 1}/{tries}) → {wait}s 대기", file=sys.stderr)
            if e.code not in (429, 500, 502, 503, 504):
                break
            time.sleep(min(wait, 90))
            delay *= 2
        except Exception as e:  # 네트워크 오류
            print(f"  [{source}] {type(e).__name__}: {e} (시도 {attempt + 1}/{tries})", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    status[source]["fail"] += 1
    return None


# ---------------------------------------------------------------- 텍스트 유틸
def norm_name(s: str) -> str:
    s = html.unescape(s or "").lower().replace("&", "and")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


HYPHENS = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212\u00ad"), "-")


def clean_title(t: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", t or "")).translate(HYPHENS)
    return re.sub(r"\s+", " ", t).strip()


def safe_filename(s: str, limit: int = 180) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    return s[:limit].rstrip(". ")


def abstract_from_inverted(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    pos.sort()
    return " ".join(w for _, w in pos)


def strip_jats(s: str) -> str:
    s = re.sub(r"<jats:title>.*?</jats:title>", " ", s or "", flags=re.S)
    return clean_title(s)


# ---------------------------------------------------------------- 설정
def load_config() -> dict:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    jmap = {}
    for j in cfg["journals"]:
        for n in [j["name"], *j.get("aliases", [])]:
            jmap[norm_name(n)] = j
    cfg["_jmap"] = jmap
    return cfg


def match_journal(cfg: dict, name: str) -> dict | None:
    return cfg["_jmap"].get(norm_name(name))


def score_topics(cfg: dict, title: str, abstract: str) -> tuple[dict, str | None, int, int]:
    """주제별 점수. 제목 일치 2점, 초록 일치 1점."""
    def variants(text: str) -> tuple[str, str]:
        text = (text or "").translate(HYPHENS)
        low = " " + text.lower() + " "
        return low, " " + re.sub(r"[^a-z0-9+]+", " ", text.lower()) + " "

    t1, t2 = variants(title)
    a1, a2 = variants(abstract)
    scores, title_hits = {}, {}
    for tp in cfg["topics"]:
        s = th = 0
        for term in tp["terms"]:
            term_l = term.lower()
            if term_l in t1 or term_l in t2:
                s += 2
                th += 1
            elif term_l in a1 or term_l in a2:
                s += 1
        if s:
            scores[tp["name"]] = s
            title_hits[tp["name"]] = th
    if not scores:
        return {}, None, 0, 0
    best = max(scores, key=lambda k: (scores[k], title_hits[k]))
    return scores, best, scores[best], title_hits[best]


# ---------------------------------------------------------------- OpenAlex
OA_SELECT = "id,doi,title,publication_date,type,primary_location,authorships,abstract_inverted_index,best_oa_location,open_access"


def openalex_query(query: str, from_date: str) -> list[dict] | None:
    flt = f"title_and_abstract.search:{query.replace(',', ' ')},from_publication_date:{from_date},type:article"
    params = {"filter": flt, "per-page": "200", "select": OA_SELECT, "sort": "publication_date:desc"}
    if EMAIL:
        params["mailto"] = EMAIL
    if OPENALEX_KEY:
        params["api_key"] = OPENALEX_KEY
    data = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), "openalex")
    if data is None:
        return None
    return data.get("results", [])


def from_openalex(w: dict) -> dict | None:
    doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
    if not doi:
        return None
    src = ((w.get("primary_location") or {}).get("source") or {})
    auths = w.get("authorships") or []
    names = [((a.get("author") or {}).get("display_name") or "").strip() for a in auths]
    corr = [n for a, n in zip(auths, names) if a.get("is_corresponding") and n]
    best = w.get("best_oa_location") or {}
    return {
        "doi": doi,
        "titleEn": clean_title(w.get("title")),
        "journal": src.get("display_name") or "",
        "pubDate": w.get("publication_date") or "",
        "authors": [n for n in names if n],
        "corrAuthors": corr,
        "abstract": abstract_from_inverted(w.get("abstract_inverted_index")),
        "pdfUrl": best.get("pdf_url") or "",
        "oaStatus": (w.get("open_access") or {}).get("oa_status") or "",
        "source": "openalex",
    }


# ---------------------------------------------------------------- Crossref (대체 경로)
CR_SELECT = "DOI,title,container-title,author,published,published-online,issued,abstract,link,type"


def crossref_query(query: str, from_date: str) -> list[dict] | None:
    params = {
        "query.bibliographic": query,
        "filter": f"from-online-pub-date:{from_date},type:journal-article",
        "rows": "100",
        "select": CR_SELECT,
        "sort": "published",
        "order": "desc",
    }
    if EMAIL:
        params["mailto"] = EMAIL
    data = get_json("https://api.crossref.org/works?" + urllib.parse.urlencode(params), "crossref")
    if data is None:
        return None
    return (data.get("message") or {}).get("items", [])


def cr_date(it: dict) -> str:
    for k in ("published-online", "published", "issued"):
        parts = ((it.get(k) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            y, m, d = (list(parts) + [1, 1])[:3]
            return f"{y:04d}-{m:02d}-{d:02d}"
    return ""


def from_crossref(it: dict) -> dict | None:
    doi = (it.get("DOI") or "").lower()
    if not doi:
        return None
    names = []
    for a in it.get("author") or []:
        n = " ".join(x for x in [a.get("given"), a.get("family")] if x).strip() or a.get("name", "")
        if n:
            names.append(n)
    pdf = ""
    for ln in it.get("link") or []:
        if (ln.get("content-type") == "application/pdf") and ln.get("URL"):
            pdf = ln["URL"]
            break
    return {
        "doi": doi,
        "titleEn": clean_title((it.get("title") or [""])[0]),
        "journal": (it.get("container-title") or [""])[0],
        "pubDate": cr_date(it),
        "authors": names,
        "corrAuthors": [],
        "abstract": strip_jats(it.get("abstract") or ""),
        "pdfUrl": "",  # Crossref link는 구독 PDF일 수 있어 OA로 쓰지 않음
        "publisherPdf": pdf,
        "oaStatus": "",
        "source": "crossref",
    }


# ---------------------------------------------------------------- Unpaywall
def unpaywall_pdf(doi: str) -> str:
    if not EMAIL:
        return ""
    data = get_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(EMAIL)}", "unpaywall", tries=2)
    if not data:
        return ""
    loc = data.get("best_oa_location") or {}
    return loc.get("url_for_pdf") or ""


# ---------------------------------------------------------------- 상태 파일
def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> int:
    cfg = load_config()
    today = dt.date.fromisoformat(os.environ["FEED_TODAY"]) if os.environ.get("FEED_TODAY") else dt.datetime.now(KST).date()
    from_date = (today - dt.timedelta(days=int(cfg.get("lookback_days", 10)))).isoformat()
    state = load_json(STATE_FILE, {"seen": {}})
    seen: dict = state.get("seen", {})
    out_path = CAND_DIR / f"{today.isoformat()}.json"
    existing = load_json(out_path, None)
    already_today = {p["doi"] for p in (existing or {}).get("papers", [])}

    print(f"기준일 {today} · 검색 시작일 {from_date} · 이미 본 DOI {len(seen)}개")

    raw: dict[str, dict] = {}
    hits_by_doi: dict[str, set] = {}
    for tp in cfg["topics"]:
        for q in tp["queries"]:
            items = openalex_query(q, from_date)
            recs = [from_openalex(w) for w in items] if items is not None else None
            if recs is None:  # OpenAlex 실패 → Crossref
                cr = crossref_query(q, from_date)
                recs = [from_crossref(it) for it in cr] if cr is not None else []
            for r in recs:
                if not r:
                    continue
                d = r["doi"]
                hits_by_doi.setdefault(d, set()).add(tp["name"])
                if d not in raw or (raw[d]["source"] == "crossref" and r["source"] == "openalex"):
                    raw[d] = r
            time.sleep(1.0)

    papers, others = [], []
    for d, r in raw.items():
        if (d in seen and d not in already_today) or not r["titleEn"]:
            continue
        if d.startswith("10.1002/ange.") and d.replace("/ange.", "/anie.") in raw:
            continue  # Angewandte 독일어판 중복
        if r["pubDate"] and r["pubDate"] < from_date:
            continue
        scores, best, best_score, title_hits = score_topics(cfg, r["titleEn"], r["abstract"])
        if not best:
            continue
        j = match_journal(cfg, r["journal"])
        if not j:
            if title_hits:
                others.append({"doi": d, "titleEn": r["titleEn"], "journal": r["journal"], "pubDate": r["pubDate"], "topic": best})
            continue
        corr = r["corrAuthors"][-1] if r["corrAuthors"] else (r["authors"][-1] if r["authors"] else "")
        corr_confirmed = bool(r["corrAuthors"])
        year = (r["pubDate"] or str(today.year))[:4]
        papers.append({
            "doi": d,
            "url": f"https://doi.org/{d}",
            "titleEn": r["titleEn"],
            "journal": j["name"],
            "journalAbbr": j["abbr"],
            "tier": j["tier"],
            "pubDate": r["pubDate"],
            "year": year,
            "authors": r["authors"][:30],
            "corrAuthor": corr + ("" if corr_confirmed or not corr else " (추정)"),
            "corrConfirmed": corr_confirmed,
            "abstract": r["abstract"][:3000],
            "pdfUrl": r["pdfUrl"],
            "oaStatus": r["oaStatus"],
            "topic": best,
            "topicScores": scores,
            "queryTopics": sorted(hits_by_doi.get(d, [])),
            "score": best_score,
            "autoPdf": bool(j["tier"] <= 2 and title_hits >= 1) or best_score >= 4,
            "filename": safe_filename(f"{corr}, {j['abbr']}, {year}, {r['titleEn']}"),
            "source": r["source"],
        })

    papers.sort(key=lambda p: (p["tier"], -p["score"], p["pubDate"] or ""), reverse=False)
    papers = papers[: int(cfg.get("max_candidates", 80))]

    for p in papers:
        if not p["pdfUrl"]:
            p["pdfUrl"] = unpaywall_pdf(p["doi"])
            time.sleep(0.2)

    if existing:  # 같은 날 두 번째 실행이면 합치기
        keep = {p["doi"]: p for p in existing.get("papers", [])}
        for p in papers:
            keep[p["doi"]] = p
        papers = sorted(keep.values(), key=lambda p: (p["tier"], -p["score"]))
        oth = {o["doi"]: o for o in existing.get("others", [])}
        for o in others:
            oth[o["doi"]] = o
        others = list(oth.values())

    result = {
        "date": today.isoformat(),
        "generatedAt": dt.datetime.now(KST).isoformat(timespec="seconds"),
        "searchFrom": from_date,
        "sources": status,
        "count": len(papers),
        "papers": papers,
        "others": others[:60],
        "note": "papers=설정 저널 소속 후보(관련도 사전 판정만 한 상태), others=설정 밖 저널(제목 일치만). 최종 선별·요약은 Claude 예약 작업이 합니다.",
    }

    ok_any = status["openalex"]["ok"] + status["crossref"]["ok"] > 0
    if not ok_any:
        print("모든 수집 경로가 실패했습니다.", file=sys.stderr)

    CAND_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    stamp = today.isoformat()
    for p in papers:
        seen.setdefault(p["doi"], stamp)
    cutoff = (today - dt.timedelta(days=60)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["lastRun"] = result["generatedAt"]
    state["lastStatus"] = status
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=0), encoding="utf-8")

    idx = load_json(INDEX_FILE, {"files": []})
    files = sorted(set(idx.get("files", [])) | {stamp})[-90:]
    INDEX_FILE.write_text(json.dumps({"latest": files[-1], "files": files, "updatedAt": result["generatedAt"]}, ensure_ascii=False, indent=1), encoding="utf-8")

    # 오래된 후보 파일 정리(90일)
    old = (today - dt.timedelta(days=90)).isoformat()
    for f in CAND_DIR.glob("*.json"):
        if f.stem < old:
            f.unlink()

    print(f"후보 {len(papers)}편 (설정 밖 저널 {len(others)}편) · 출처 상태 {status}")
    return 0 if ok_any else 2


if __name__ == "__main__":
    sys.exit(main())
