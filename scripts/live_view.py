"""Serve the animated job-radar page with the live state of the current collect.

    python scripts/live_view.py          then open http://127.0.0.1:8770/

One poller asks Airflow's database and the warehouse every 3 seconds (read-only psql through docker exec) and keeps the
answer in memory; the page reads it from /state. Nothing here writes anywhere.
"""
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(r"E:\Desktop-Archive\Argus\argus-session-html\job-radar-step-by-step.html")
PORT, EVERY = 8770, 3
PGOPTIONS = "PGOPTIONS=-c statement_timeout=5000"

AIRFLOW = """
WITH r AS (SELECT * FROM dag_run WHERE dag_id = 'job_radar'
           ORDER BY (state = 'running') DESC, coalesce(start_date, queued_at) DESC LIMIT 1)
SELECT json_build_object(
  'now', now(),
  'run', (SELECT row_to_json(x) FROM (SELECT run_id, state, run_type, queued_at, start_date, end_date FROM r) x),
  'tasks', (SELECT coalesce(json_agg(json_build_object('id', task_id, 'state', state, 'start', start_date,
                                                       'end', end_date, 'try', try_number)), '[]'::json)
            FROM task_instance WHERE dag_id = 'job_radar' AND run_id = (SELECT run_id FROM r)),
  'dags', (SELECT coalesce(json_object_agg(dag_id, json_build_object('run_id', run_id, 'state', state,
                                                                      'start', start_date, 'end', end_date)), '{}'::json)
           FROM (SELECT DISTINCT ON (dag_id) dag_id, run_id, state, start_date, end_date FROM dag_run
                 WHERE dag_id IN ('job_radar_email_egypt', 'job_radar_email_abroad', 'job_radar_applications')
                 ORDER BY dag_id, coalesce(start_date, queued_at) DESC) d));
"""
WAREHOUSE = """
SELECT coalesce(json_agg(json_build_object('step', step, 'status', status, 'summary', left(summary, 220),
                                           'started', started_at, 'finished', finished_at, 'cost', cost_usd)), '[]'::json)
FROM (SELECT DISTINCT ON (step) * FROM core.step_run ORDER BY step, started_at DESC) s;
"""

state = {"error": "starting"}


def psql(container, user, db, sql):
    out = subprocess.run(["docker", "exec", "-e", PGOPTIONS, container, "psql", "-U", user,
                          "-d", db, "-At", "-c", sql], capture_output=True, text=True, encoding="utf-8", timeout=15)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200] or "psql failed")
    return json.loads(out.stdout.strip().splitlines()[-1])


def poll():
    global state
    while True:
        try:
            s = psql("job-radar-airflow-db-1", "airflow", "airflow", AIRFLOW)
            s["steps"] = psql("job-radar-warehouse-1", "jobradar", "jobradar", WAREHOUSE)
            state = s
        except Exception as e:  # docker stopped, db restarting: say so, keep polling
            state = {"error": str(e)[:200]}
        time.sleep(EVERY)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/state":
            body, kind = json.dumps(state).encode(), "application/json"
        elif self.path.split("?")[0] in ("/", "/index.html"):
            body, kind = PAGE.read_bytes(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    print(f"http://127.0.0.1:{PORT}/  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
