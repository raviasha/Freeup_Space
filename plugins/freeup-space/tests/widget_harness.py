"""Development-only MCP Apps browser host; all moves are simulated on fixtures."""
import json
import os
import time
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from freeup_space.widget_server import Server
from freeup_space.widget_service import ROOT

temp = tempfile.TemporaryDirectory(prefix="freeup-widget-test-")
directory = Path(temp.name).resolve()
workspace = directory / "workspace"
workspace.mkdir()
files = directory / "Downloads"
files.mkdir()
for i in range(60):
    (files / ("sample-%02d.zip" % i)).write_bytes(bytes([i]) * 4096)
(files / "example.dmg").write_bytes(b"installer" * 500)
(files / "unknown.bin").write_bytes(b"unknown" * 500)
server = Server()
original = server.service.command


def fixture_command(session, args, progress=False):
    if args[0] == "apply" and "--dry-run" not in args:
        ids = args[3:]
        candidates = {c["candidate_id"]: c for c in session["plan"]["candidates"]}
        return {"moved": [dict(candidate_id=i, path=candidates[i]["path"], bytes_moved=candidates[i]["reclaimable_bytes"]) for i in ids],
                "failed": [], "skipped": [], "logical_moved_bytes": sum(candidates[i]["reclaimable_bytes"] for i in ids),
                "free_space_before": 1000000, "free_space_after": 1000000}
    if args[0] == "start":
        time.sleep(float(os.environ.get("FREEUP_TEST_SCAN_DELAY", "0")))
        args = args[:args.index("--")+1] + [str(files)]
    return original(session, args, progress)


server.service.command = fixture_command
HOST = '''<!doctype html><html><head><title>Freeup Space widget tests</title><style>body{margin:20px;background:#e8ece9;font:14px system-ui}iframe{display:block;border:0;background:white;width:900px;height:1000px;margin:20px auto;border-radius:16px}p{text-align:center}</style></head><body><p>Test host · temporary sample files · simulated Trash</p><iframe id="widget" src="/widget" title="Freeup Space"></iframe><script>
const frame=document.querySelector('iframe');let view;
async function api(name,args){const r=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,args})});return await r.json()}
window.addEventListener('message',async e=>{if(e.source!==frame.contentWindow)return;const d=e.data;let result={};
if(d.method==='ui/initialize')result={protocolVersion:'2026-01-26',hostInfo:{name:'Fixture host',version:'1'},hostCapabilities:{serverTools:{},serverResources:{},message:{text:{}}}};
else if(d.method==='ui/notifications/initialized'){view ||= await api('open_widget',{});frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:view},'*');return}
else if(d.method==='tools/call')result=await api(d.params.name,d.params.arguments);
else if(d.method==='ui/message'){window.aiPrompt=d.params.content[0].text;result={};}
else return;
if(d.id!==undefined)frame.contentWindow.postMessage({jsonrpc:'2.0',id:d.id,result},'*');});
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, payload, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/":
            self.send(HOST.encode(), "text/html; charset=utf-8")
        elif self.path == "/widget":
            self.send((ROOT / "assets/widget.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api" or urlparse(self.headers.get("Origin", "")).netloc != self.headers.get("Host"):
            self.send_error(403)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if size > 100000:
            self.send_error(413)
            return
        data = json.loads(self.rfile.read(size))
        if data["name"] == "open_widget":
            data["args"] = {"workspace": str(workspace)}
        result = server.dispatch({"method": "tools/call", "params": {"name": data["name"], "arguments": data["args"]}})
        self.send(json.dumps(result).encode(), "application/json")


if __name__ == "__main__":
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print("http://127.0.0.1:%d" % httpd.server_port, flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        server.service.close()
        temp.cleanup()
