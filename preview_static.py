#!/usr/bin/env python3
"""Preview용 단순 정적 서버 — PORT env로 시작."""
import os, http.server, socketserver
socketserver.TCPServer.allow_reuse_address = True
port = int(os.environ.get("PORT", "8830"))
print(f"static {port}", flush=True)
socketserver.TCPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler).serve_forever()
