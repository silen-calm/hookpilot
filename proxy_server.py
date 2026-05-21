#!/usr/bin/env python3
"""Preview용 proxy server — 8810 port에서 9000 download_server로 모든 요청 forward.
Preview tool이 진짜 사장님 화면 검증할 수 있게."""
import http.server, socketserver, urllib.request, urllib.error

UPSTREAM = "http://127.0.0.1:9000"


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self._proxy()
    def do_HEAD(self): self._proxy()
    def do_OPTIONS(self): self._proxy()

    def _proxy(self):
        try:
            upstream_url = UPSTREAM + self.path
            req = urllib.request.Request(upstream_url, headers={"User-Agent": self.headers.get("User-Agent") or "preview"})
            with urllib.request.urlopen(req, timeout=30) as r:
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() in ("connection", "transfer-encoding", "content-length"):
                        continue
                    self.send_header(k, v)
                body = r.read()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            try: self.wfile.write(e.read())
            except: pass
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"proxy err: {e}".encode())

    def log_message(self, format, *args): pass


if __name__ == "__main__":
    import os
    socketserver.TCPServer.allow_reuse_address = True
    port = int(os.environ.get("PORT", "8810"))
    print(f"proxy {port} → 9000", flush=True)
    socketserver.TCPServer(("127.0.0.1", port), ProxyHandler).serve_forever()
