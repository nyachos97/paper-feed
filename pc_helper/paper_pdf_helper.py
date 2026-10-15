#!/usr/bin/env python3
"""논문 PDF 도우미 (Windows, 아주대 기관 네트워크용).

하는 일
1) 자동 저장: GitHub 저장소의 후보 목록(data/candidates/*.json) 중 autoPdf 논문을
   기관 네트워크로 받아 "교신저자, 저널 약어, 연도, 제목.pdf" 이름으로 저장합니다.
   컴퓨터가 꺼져 있던 날의 목록도 다음에 켤 때 이어서 처리합니다.
2) 'PDF 저장' 버튼: 피드 페이지에서 'PDF 저장'을 누르면 복사되는 PAPERDL:: 정보를
   클립보드에서 감지해 바로 받습니다. 직접 받을 수 없으면 논문 페이지를 브라우저로
   열고, 15분 안에 다운로드 폴더에 새 PDF가 생기면 이름을 바꿔 옮긴 뒤 엽니다.

로봇 확인(CAPTCHA)·로그인 페이지는 우회하지 않고 '직접 받기 목록'에 남깁니다.
표준 라이브러리만 사용합니다(Python 3.9+).
"""
from __future__ import annotations

import base64
import datetime as dt
import http.cookiejar
import json
import os
import re
import shutil
import socket
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "helper_config.json"
STATE_FILE = HERE / "helper_state.json"
LOG_FILE = HERE / "helper.log"

DEFAULT_CONFIG = {
    "github_repo": "",               # 예: "nyachos97/paper-feed"
    "branch": "main",
    "save_dir": "E:\\★★★★★\\논문",
    "downloads_dir": str(Path.home() / "Downloads"),
    "auto_download": True,
    "max_auto_per_run": 25,
    "poll_minutes": 30,
    "open_after_manual_save": True,
    "elsevier_api_key": "",          # 선택: dev.elsevier.com에서 무료 발급 (ScienceDirect PDF 자동 저장용)
    "contact_email": "",             # 선택: Unpaywall 조회용
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 paper-pdf-helper/1.0")


# ---------------------------------------------------------------- 기본 유틸
def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if LOG_FILE.stat().st_size > 2_000_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".old.log"))
    except Exception:
        pass


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(p: Path, obj) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_FILE, {}))
    if not CONFIG_FILE.exists():
        save_json(CONFIG_FILE, cfg)
    return cfg


def safe_filename(s: str, limit: int = 180) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", s or "paper")
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    return (s[:limit].rstrip(". ") or "paper")


def unique_path(folder: Path, name: str) -> Path:
    p = folder / f"{name}.pdf"
    return p


# ---------------------------------------------------------------- HTTP
_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))


def http_get(url: str, headers: dict | None = None, timeout: int = 60) -> tuple[bytes, str, str]:
    h = {"User-Agent": UA, "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8", "Accept-Language": "ko,en;q=0.8"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with _opener.open(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", ""), r.geturl()


def fetch_json(url: str):
    body, _, _ = http_get(url, {"Accept": "application/json"})
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------- PDF 주소 후보
def pdf_candidates(doi: str, pdf_url: str, cfg: dict) -> list[tuple[str, dict]]:
    doi = doi.strip()
    q = urllib.parse.quote(doi, safe="/")
    out: list[tuple[str, dict]] = []
    if pdf_url:
        out.append((pdf_url, {}))
    pre = doi.split("/")[0]
    if pre == "10.1021":                      # ACS
        out.append((f"https://pubs.acs.org/doi/pdf/{q}", {}))
    elif pre == "10.1002":                    # Wiley
        out.append((f"https://onlinelibrary.wiley.com/doi/pdfdirect/{q}", {}))
        out.append((f"https://onlinelibrary.wiley.com/doi/pdf/{q}", {}))
    elif pre in ("10.1038", "10.1007", "10.1186"):  # Nature / Springer
        suffix = doi.split("/", 1)[1]
        if pre == "10.1038":
            out.append((f"https://www.nature.com/articles/{suffix}.pdf", {}))
        else:
            out.append((f"https://link.springer.com/content/pdf/{q}.pdf", {}))
    elif pre == "10.1126":                    # Science / Sci. Adv.
        out.append((f"https://www.science.org/doi/pdf/{q}", {}))
    elif pre == "10.1016":                    # Elsevier: 공식 API (기관 IP + 무료 API 키)
        if cfg.get("elsevier_api_key"):
            out.append((f"https://api.elsevier.com/content/article/doi/{q}?httpAccept=application/pdf",
                        {"X-ELS-APIKey": cfg["elsevier_api_key"], "Accept": "application/pdf"}))
    elif pre == "10.1039":                    # RSC: 랜딩 페이지에서 PDF 링크를 찾습니다
        out.append((f"__rsc__{doi}", {}))
    if cfg.get("contact_email"):
        out.append((f"__unpaywall__{doi}", {}))
    return out


def try_download(doi: str, pdf_url: str, cfg: dict) -> tuple[bytes | None, str]:
    """PDF 바이트와 사유를 돌려줍니다. 로봇 확인/로그인 페이지는 우회하지 않습니다."""
    reasons = []
    for url, hdr in pdf_candidates(doi, pdf_url, cfg):
        try:
            if url.startswith("__unpaywall__"):
                d = fetch_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(cfg['contact_email'])}")
                url = ((d or {}).get("best_oa_location") or {}).get("url_for_pdf") or ""
                if not url:
                    continue
            if url.startswith("__rsc__"):
                body, _, final = http_get(f"https://doi.org/{doi}")
                m = re.search(rb'href="([^"]+/articlepdf/[^"]+)"', body)
                if not m:
                    reasons.append("RSC PDF 링크 없음")
                    continue
                url = urllib.parse.urljoin(final, m.group(1).decode())
            body, ctype, final = http_get(url, hdr)
            if body[:5] == b"%PDF-":
                return body, ""
            text = body[:4000].decode("utf-8", "ignore").lower()
            if any(k in text for k in ("captcha", "cf-challenge", "are you a robot", "verify you are human", "just a moment")):
                reasons.append(f"로봇 확인 페이지({urllib.parse.urlparse(final).netloc})")
            elif any(k in text for k in ("sign in", "log in", "institutional login", "purchase")):
                reasons.append(f"로그인/구독 필요({urllib.parse.urlparse(final).netloc})")
            else:
                reasons.append(f"PDF가 아닌 응답({ctype or '?'})")
        except urllib.error.HTTPError as e:
            reasons.append(f"HTTP {e.code}({urllib.parse.urlparse(url).netloc})")
        except Exception as e:
            reasons.append(f"{type(e).__name__}")
        time.sleep(2)  # 출판사 서버에 부담 주지 않도록
    return None, "; ".join(reasons) or "PDF 주소 없음"


def save_pdf(data: bytes, cfg: dict, name: str) -> Path:
    folder = Path(cfg["save_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    path = unique_path(folder, safe_filename(name))
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- 직접 받기 목록
def write_manual_list(cfg: dict, state: dict) -> None:
    items = state.get("manual", {})
    folder = Path(cfg["save_dir"])
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    rows = []
    for doi, it in sorted(items.items(), key=lambda kv: kv[1].get("date", ""), reverse=True)[:100]:
        rows.append(
            f"<li><a href='https://doi.org/{doi}'>{it.get('name', doi)}</a> "
            f"<small>· {it.get('date', '')} · {it.get('reason', '')}</small></li>")
    html_doc = ("<!doctype html><meta charset='utf-8'><title>직접 받기 목록</title>"
                "<style>body{font-family:'Malgun Gothic',sans-serif;max-width:900px;margin:24px auto;padding:0 16px}"
                "li{margin:8px 0}small{color:#666}</style>"
                "<h1>직접 받아야 하는 논문</h1><p>자동 저장이 안 된 논문입니다(로봇 확인·구독 범위 밖 등). "
                "피드 페이지에서 해당 논문의 'PDF 저장'을 누르면 논문 페이지가 열리고, 거기서 받은 PDF는 도우미가 이름을 바꿔 옮깁니다.</p>"
                f"<ul>{''.join(rows) or '<li>없음</li>'}</ul>")
    try:
        (folder / "_직접받기_목록.html").write_text(html_doc, encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- GitHub 동기화(자동 저장)
def raw_url(cfg: dict, path: str) -> str:
    return f"https://raw.githubusercontent.com/{cfg['github_repo']}/{cfg.get('branch', 'main')}/{path}"


def sync_github(cfg: dict, state: dict) -> None:
    if not cfg.get("github_repo") or not cfg.get("auto_download"):
        return
    try:
        idx = fetch_json(raw_url(cfg, "data/index.json"))
    except Exception as e:
        log(f"[동기화] index.json 읽기 실패: {e}")
        return
    done_files = set(state.setdefault("processed_files", []))
    files = [f for f in idx.get("files", []) if f not in done_files or f == idx.get("latest")]
    budget = int(cfg.get("max_auto_per_run", 25))
    for day in files[-14:]:
        try:
            cand = fetch_json(raw_url(cfg, f"data/candidates/{day}.json"))
        except Exception as e:
            log(f"[동기화] {day} 읽기 실패: {e}")
            continue
        left = False
        for p in cand.get("papers", []):
            if not p.get("autoPdf"):
                continue
            doi = p["doi"]
            if doi in state.setdefault("saved", {}) or doi in state.setdefault("manual", {}):
                continue
            if budget <= 0:
                log("[동기화] 이번 회차 한도에 도달 — 다음 회차에 이어서 처리")
                save_json(STATE_FILE, state)
                return
            budget -= 1
            name = p.get("filename") or p.get("titleEn") or doi
            target = Path(cfg["save_dir"]) / f"{safe_filename(name)}.pdf"
            if target.exists():
                state["saved"][doi] = {"path": str(target), "date": day}
                continue
            data, why = try_download(doi, p.get("pdfUrl", ""), cfg)
            if data:
                path = save_pdf(data, cfg, name)
                state["saved"][doi] = {"path": str(path), "date": day}
                log(f"[자동 저장] {path.name}")
            else:
                tries = state.setdefault("tries", {})
                tries[doi] = tries.get(doi, 0) + 1
                if tries[doi] >= 3 or any(k in why for k in ("로봇", "로그인", "주소 없음")):
                    state["manual"][doi] = {"name": name, "date": day, "reason": why}
                    log(f"[직접 받기] {doi} — {why}")
                else:
                    left = True
                    log(f"[재시도 예정 {tries[doi]}/3] {doi} — {why}")
            save_json(STATE_FILE, state)
        if day != idx.get("latest") and not left:
            done_files.add(day)
    state["processed_files"] = sorted(done_files)[-120:]
    write_manual_list(cfg, state)
    save_json(STATE_FILE, state)


# ---------------------------------------------------------------- 'PDF 저장' 버튼(클립보드)
def parse_token(text: str) -> dict | None:
    if not text or not text.startswith("PAPERDL::"):
        return None
    try:
        return json.loads(base64.b64decode(text[9:].strip()).decode("utf-8"))
    except Exception:
        return None


class Clipboard:
    def __init__(self):
        import tkinter as tk
        self.tk = tk.Tk()
        self.tk.withdraw()

    def get(self) -> str:
        try:
            self.tk.update()
            return self.tk.clipboard_get()
        except Exception:
            return ""

    def set(self, text: str) -> None:
        try:
            self.tk.clipboard_clear()
            self.tk.clipboard_append(text)
            self.tk.update()
        except Exception:
            pass


def open_file(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        webbrowser.open(path.as_uri())


def handle_token(tok: dict, cfg: dict, state: dict, pending: list) -> None:
    doi = tok.get("doi", "")
    name = tok.get("name") or doi or "paper"
    target = Path(cfg["save_dir"]) / f"{safe_filename(name)}.pdf"
    if target.exists():
        log(f"[버튼] 이미 있음 → 열기: {target.name}")
        open_file(target)
        return
    data, why = (try_download(doi, tok.get("pdf", ""), cfg) if doi else (None, "DOI 없음"))
    if data:
        path = save_pdf(data, cfg, name)
        state.setdefault("saved", {})[doi] = {"path": str(path), "date": dt.date.today().isoformat()}
        state.get("manual", {}).pop(doi, None)
        save_json(STATE_FILE, state)
        log(f"[버튼 저장] {path.name}")
        if cfg.get("open_after_manual_save", True):
            open_file(path)
        return
    log(f"[버튼] 직접 받기 필요({why}) → 논문 페이지를 엽니다. 15분 안에 받은 PDF를 자동으로 옮깁니다.")
    dl = Path(cfg["downloads_dir"])
    before = {p.name for p in dl.glob("*.pdf")} if dl.exists() else set()
    pending.append({"doi": doi, "name": name, "before": before, "start": time.time(), "deadline": time.time() + 900})
    webbrowser.open(tok.get("url") or f"https://doi.org/{doi}")


def watch_downloads(cfg: dict, state: dict, pending: list) -> None:
    if not pending:
        return
    dl = Path(cfg["downloads_dir"])
    now = time.time()
    for job in list(pending):
        if now > job["deadline"]:
            log(f"[버튼] 시간 초과: {job['name']}")
            pending.remove(job)
            continue
        if not dl.exists():
            continue
        new = [p for p in dl.glob("*.pdf")
               if p.name not in job["before"] and p.stat().st_mtime >= job["start"] - 5]
        for p in sorted(new, key=lambda x: x.stat().st_mtime):
            s1 = p.stat().st_size
            time.sleep(1.5)
            if not p.exists() or p.stat().st_size != s1 or s1 < 1000:
                continue  # 아직 받는 중
            with p.open("rb") as fh:
                if fh.read(5) != b"%PDF-":
                    continue
            folder = Path(cfg["save_dir"])
            folder.mkdir(parents=True, exist_ok=True)
            target = unique_path(folder, safe_filename(job["name"]))
            shutil.move(str(p), str(target))
            state.setdefault("saved", {})[job["doi"]] = {"path": str(target), "date": dt.date.today().isoformat()}
            state.get("manual", {}).pop(job["doi"], None)
            save_json(STATE_FILE, state)
            write_manual_list(cfg, state)
            log(f"[버튼] 다운로드 폴더에서 옮김: {target.name}")
            if cfg.get("open_after_manual_save", True):
                open_file(target)
            pending.remove(job)
            break


# ---------------------------------------------------------------- 메인 루프
def single_instance() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47615))
        s.listen(1)
        return s
    except OSError:
        return None


def main() -> int:
    once = "--once" in sys.argv
    cfg = load_config()
    state = load_json(STATE_FILE, {"saved": {}, "manual": {}, "processed_files": []})
    if not once:
        lock = single_instance()
        if lock is None:
            print("이미 실행 중입니다.")
            return 0
    log(f"도우미 시작 · 저장 폴더 {cfg['save_dir']} · 저장소 {cfg.get('github_repo') or '(미설정)'}")
    if not cfg.get("github_repo"):
        log(f"helper_config.json에 github_repo를 입력하면 자동 저장이 켜집니다: {CONFIG_FILE}")

    if once:
        sync_github(cfg, state)
        return 0

    clip = Clipboard()
    pending: list = []
    last_sync = 0.0
    last_clip = clip.get()
    while True:
        try:
            if time.time() - last_sync > max(5, int(cfg.get("poll_minutes", 30))) * 60:
                cfg = load_config()
                sync_github(cfg, state)
                last_sync = time.time()
            cur = clip.get()
            if cur and cur != last_clip:
                last_clip = cur
                tok = parse_token(cur)
                if tok:
                    log(f"[버튼] 요청 받음: {tok.get('name') or tok.get('doi')}")
                    clip.set("")  # 같은 요청이 두 번 처리되지 않게
                    last_clip = ""
                    handle_token(tok, cfg, state, pending)
            watch_downloads(cfg, state, pending)
        except KeyboardInterrupt:
            return 0
        except Exception:
            log("오류: " + traceback.format_exc().splitlines()[-1])
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
