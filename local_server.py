"""
Local sync server - run this to enable the "עדכן עכשיו" button in the dashboard.
Listens on http://localhost:8765
"""
import http.server
import subprocess
import os
import threading

PORT = 8765
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        if self.path.startswith('/sync'):
            self.wfile.write('<html><body dir="rtl" style="font-family:Arial;text-align:center;padding:40px"><h2>🔄 מסנכרן מלאי...</h2></body></html>'.encode('utf-8'))
            # Run sync in background
            def run():
                subprocess.run(['python', 'sync.py'], cwd=SCRIPT_DIR)
                subprocess.run(['git', 'add', 'docs/data.json', 'docs/search.json'], cwd=SCRIPT_DIR)
                subprocess.run(['git', 'commit', '-m', 'Manual sync'], cwd=SCRIPT_DIR)
                subprocess.run(['git', 'push'], cwd=SCRIPT_DIR)
            threading.Thread(target=run, daemon=True).start()
        else:
            self.wfile.write('<html><body>Hasidim Sync Server running</body></html>'.encode('utf-8'))

    def log_message(self, format, *args):
        pass  # silence logs

print(f'Sync server running on http://localhost:{PORT}')
print('Keep this window open to enable the "עדכן עכשיו" button')
with http.server.HTTPServer(('localhost', PORT), Handler) as httpd:
    httpd.serve_forever()
