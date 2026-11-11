"""digest/ 폴더의 요약 파일을 보여 주는 로컬 정적 서버.

사용법:
    python serve.py            # http://localhost:8000
    python serve.py 8080       # 포트 지정

실행할 때마다 digest/index.json(파일 목록)을 새로 만든 뒤
이 폴더를 정적 파일로 서빙한다. localhost에서만 접속을 받는다.
"""

from __future__ import annotations

import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIGEST_DIR = BASE_DIR / "digest"


def build_index() -> list[str]:
    DIGEST_DIR.mkdir(exist_ok=True)
    files = sorted((p.name for p in DIGEST_DIR.glob("*.md")), reverse=True)  # 최신 날짜 먼저
    (DIGEST_DIR / "index.json").write_text(
        json.dumps({"files": files}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return files


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".md": "text/markdown; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }

    def do_GET(self):
        # 목록 요청 때마다 새로 만들어, 서버를 켠 뒤 생긴 파일도 보이게 한다
        if self.path.split("?")[0] == "/digest/index.json":
            build_index()
        # 소스 코드는 서빙하지 않는다
        if self.path.split("?")[0].endswith(".py"):
            self.send_error(404)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    files = build_index()
    handler = partial(Handler, directory=str(BASE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"digest 파일 {len(files)}개 → http://localhost:{port}  (종료: Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
