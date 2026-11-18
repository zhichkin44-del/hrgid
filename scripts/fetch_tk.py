"""Загрузка текста Трудового кодекса РФ с consultant.ru (постатейно) в data/raw/.

Источник записывается в data/raw/SOURCE.json. Запуск: python scripts/fetch_tk.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

BASE = "https://www.consultant.ru"
DOC = "/document/cons_doc_LAW_34683/"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
UA = "Mozilla/5.0 (compatible; hrgid-loader/0.1)"
LINK = re.compile(
    r'href="(/document/cons_doc_LAW_34683/[0-9a-f]{40}/)"[^>]*>\s*(Статья \d+(?:\.\d+)?\.[^<]*)'
)


def get(url: str, retries: int = 4) -> str:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as resp:
                data: bytes = resp.read()
            return data.decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
            print(f"повтор {url}: {exc}", file=sys.stderr)
    raise RuntimeError("недостижимо")


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    toc = get(BASE + DOC)
    edition = re.search(r"\(ред\. от (\d{2}\.\d{2}\.\d{4})\)", toc)
    items = list(dict.fromkeys(LINK.findall(toc)))
    print(f"статей в оглавлении: {len(items)}; редакция: {edition.group(1) if edition else '?'}")

    def one(item: tuple[str, str]) -> str:
        href, title = item
        num = re.match(r"Статья (\d+(?:\.\d+)?)\.", title)
        assert num
        out = RAW / f"st_{num.group(1)}.html"
        if not out.exists():
            out.write_text(get(BASE + href), encoding="utf-8")
            time.sleep(0.4)
        return num.group(1)

    with ThreadPoolExecutor(max_workers=3) as pool:
        done = list(pool.map(one, items))
    (RAW / "SOURCE.json").write_text(
        json.dumps(
            {
                "document": "Трудовой кодекс Российской Федерации от 30.12.2001 N 197-ФЗ",
                "edition": edition.group(1) if edition else None,
                "source": BASE + DOC,
                "publisher": "КонсультантПлюс (официальный текст закона; "
                "pravo.gov.ru с этой машины недоступен)",
                "fetched": date.today().isoformat(),
                "articles": len(done),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"загружено: {len(done)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
