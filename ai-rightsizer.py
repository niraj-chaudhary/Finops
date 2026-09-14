"""
ECS utilization -> Prometheus metrics + rightsizing recommendations.

Reads ECS CPU/Memory utilization % (of **reserved** task CPU/RAM) from Prometheus
(CloudWatch exporter), then estimates **scale factors** to apply to current task
CPU units / memory (MiB):

  recommended_scale = clamp( observed_pct / target_pct, min_scale, max_scale )

If utilization is below RIGHTSIZE_MIN_OBSERVED_PERCENT, scale stays 1.0 (no signal).

Example: 35% CPU vs target 70% -> scale 0.5 (about half the current CPU reservation).

Environment:
  RIGHTSIZE_TARGET_CPU_PERCENT (default 70)
  RIGHTSIZE_TARGET_MEMORY_PERCENT (default 75)
  RIGHTSIZE_MIN_SCALE / RIGHTSIZE_MAX_SCALE (default 0.5 / 2.0)
  RIGHTSIZE_MIN_OBSERVED_PERCENT (default 5)
  RIGHTSIZE_RECOMMEND_SCALE_LT / RIGHTSIZE_RECOMMEND_SCALE_GT (default 0.92 / 1.08) → -1 / +1 hints
  PROMETHEUS_URL, ECS_* metrics (see earlier docstring)

Exports `rightsizing_recommendation_cpu` / `rightsizing_recommendation_memory` as -1, 0, or 1.

Endpoints: /metrics, /recommendations (JSON), /healthz, /debug
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Prometheus HTTP (stdlib)
# ---------------------------------------------------------------------------


def _http_get(url: str, timeout: float = 15) -> Tuple[Optional[dict], Optional[str]]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()[:500]
        except Exception:
            body = str(e)
        return None, f"HTTP {e.code}: {body}"
    except Exception as e:
        return None, str(e)


def _resolve_prometheus_url() -> str:
    explicit = os.environ.get("PROMETHEUS_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    if os.path.exists("/.dockerenv"):
        return "http://prometheus:9090"
    return "http://127.0.0.1:9090"


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


PROMETHEUS_URL = _resolve_prometheus_url()

TARGET_CPU_PCT = _env_float("RIGHTSIZE_TARGET_CPU_PERCENT", 70.0)
TARGET_MEM_PCT = _env_float("RIGHTSIZE_TARGET_MEMORY_PERCENT", 75.0)
MIN_SCALE = _env_float("RIGHTSIZE_MIN_SCALE", 0.5)
MAX_SCALE = _env_float("RIGHTSIZE_MAX_SCALE", 2.0)
MIN_OBS_PCT = _env_float("RIGHTSIZE_MIN_OBSERVED_PERCENT", 5.0)
# If scale < REC_DOWN → recommend reduce; if scale > REC_UP → recommend increase; else maintain.
REC_DOWN = _env_float("RIGHTSIZE_RECOMMEND_SCALE_LT", 0.92)
REC_UP = _env_float("RIGHTSIZE_RECOMMEND_SCALE_GT", 1.08)

CPU_METRICS = os.environ.get(
    "ECS_CPU_METRICS",
    "aws_ecs_cpu_utilization_average,aws_ecs_cpuutilization_average",
).split(",")
MEM_METRICS = os.environ.get(
    "ECS_MEMORY_METRICS",
    "aws_ecs_memory_utilization_average,aws_ecs_memoryutilization_average",
).split(",")

CPU_FALLBACK_PROMQL = os.environ.get(
    "ECS_CPU_FALLBACK_PROMQL",
    '{__name__=~"aws_ecs_cpu_?utilization_average"}',
)
MEM_FALLBACK_PROMQL = os.environ.get(
    "ECS_MEMORY_FALLBACK_PROMQL",
    '{__name__=~"aws_ecs_memory_?utilization_average"}',
)

LAST_QUERY_INFO: Dict[str, str] = {"cpu": "", "memory": ""}


def query_promql(query: str) -> Tuple[List, Optional[str]]:
    qs = urllib.parse.urlencode({"query": query.strip()})
    url = f"{PROMETHEUS_URL}/api/v1/query?{qs}"
    payload, err = _http_get(url)
    if err:
        return [], err
    if payload.get("status") != "success":
        e = payload.get("error") or payload.get("errorType") or str(payload)
        return [], f"Prometheus API: {e}"
    data = payload.get("data") or {}
    return data.get("result") or [], None


def series_match(match: str, seconds_ago: int = 3600) -> Tuple[List, Optional[str]]:
    end = int(time.time())
    start = end - seconds_ago
    qs = urllib.parse.urlencode(
        {"match[]": match, "start": str(start), "end": str(end)}
    )
    url = f"{PROMETHEUS_URL}/api/v1/series?{qs}"
    payload, err = _http_get(url, timeout=30)
    if err:
        return [], err
    if payload.get("status") != "success":
        e = payload.get("error") or payload.get("errorType") or str(payload)
        return [], f"Prometheus API: {e}"
    return payload.get("data") or [], None


def _results_to_rows(results: List) -> List[Tuple[str, float]]:
    out = []
    for r in results:
        metric = r.get("metric") or {}
        cluster = metric.get("cluster_name") or metric.get("ClusterName") or "unknown"
        service = metric.get("service_name") or metric.get("ServiceName") or "unknown"
        region = metric.get("region") or ""
        environment = metric.get("environment") or ""
        raw = (r.get("value") or ["", "0"])[1]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        suffix_parts = [p for p in (region, environment) if p]
        suffix = f"/{'/'.join(suffix_parts)}" if suffix_parts else ""
        out.append((f"{cluster}/{service}{suffix}", value))
    return out


def _log(kind: str, msg: str) -> None:
    LAST_QUERY_INFO[kind] = msg
    print(f"[{kind}] {msg}", file=sys.stderr)


def _collect(kind: str, metric_names: List[str], fallback_promql: str) -> List[Tuple[str, float]]:
    errors: List[str] = []

    def try_query(label: str, promql: str) -> Optional[List[Tuple[str, float]]]:
        results, err = query_promql(promql)
        if err:
            errors.append(f"{label}: {err}")
            _log(kind, f"{label} series=0 err={err}")
            return None
        _log(kind, f"{label} series={len(results)} err=ok")
        if results:
            return _results_to_rows(results)
        return None

    for name in metric_names:
        name = name.strip()
        if not name:
            continue
        row = try_query(f"metric={name}", name)
        if row is not None:
            return row
        row = try_query(
            f"last_over_time({name}[1h])",
            f"last_over_time({name}[1h])",
        )
        if row is not None:
            return row

    row = try_query(f"fallback={fallback_promql}", fallback_promql)
    if row is not None:
        return row
    inner = fallback_promql.strip()
    if inner.startswith("{") and inner.endswith("}"):
        row = try_query(
            f"last_over_time({inner}[1h])",
            f"last_over_time({inner}[1h])",
        )
        if row is not None:
            return row

    if errors:
        print(f"[{kind}] all attempts failed: {'; '.join(errors)}", file=sys.stderr)
    return []


def get_cpu() -> List[Tuple[str, float]]:
    return _collect("cpu", CPU_METRICS, CPU_FALLBACK_PROMQL)


def get_memory() -> List[Tuple[str, float]]:
    return _collect("memory", MEM_METRICS, MEM_FALLBACK_PROMQL)


def recommended_scale(observed_pct: float, target_pct: float) -> Tuple[float, bool]:
    """
    Returns (scale_factor, used_default_because_low_signal).
    Multiply current ECS task CPU (or memory MiB) by scale to aim near target_pct average.
    """
    if target_pct <= 0:
        return 1.0, True
    if observed_pct < MIN_OBS_PCT:
        return 1.0, True
    raw = observed_pct / target_pct
    clamped = max(MIN_SCALE, min(MAX_SCALE, raw))
    return clamped, False


def recommendation_from_scale(scale: float, low_signal: bool) -> Tuple[int, str]:
    """
    Maps scale factor to a discrete hint for dashboards and automation.
    -1 = reduce reservation, 0 = maintain or insufficient signal, 1 = increase reservation.
    """
    if low_signal:
        return 0, "no_signal"
    if scale < REC_DOWN:
        return -1, "reduce"
    if scale > REC_UP:
        return 1, "increase"
    return 0, "maintain"


def _escape_label(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_metrics_and_recommendations(
    cpu_data: List[Tuple[str, float]],
    mem_data: List[Tuple[str, float]],
) -> Tuple[str, List[dict]]:
    """Prometheus text + list of recommendation dicts for JSON."""
    lines = [
        "# HELP rightsizing_cpu_utilization ECS CPU utilization % of reserved CPU (CloudWatch).",
        "# TYPE rightsizing_cpu_utilization gauge",
        "# HELP rightsizing_memory_utilization ECS memory utilization % of reserved memory (CloudWatch).",
        "# TYPE rightsizing_memory_utilization gauge",
        "# HELP rightsizing_recommended_cpu_scale Multiply current task CPU units by this to approach target CPU utilization.",
        "# TYPE rightsizing_recommended_cpu_scale gauge",
        "# HELP rightsizing_recommended_memory_scale Multiply current task memory MiB by this to approach target memory utilization.",
        "# TYPE rightsizing_recommended_memory_scale gauge",
        "# HELP rightsizing_recommendation_cpu Suggested direction from scale: -1 reduce CPU units, 0 maintain or no_signal, 1 increase.",
        "# TYPE rightsizing_recommendation_cpu gauge",
        "# HELP rightsizing_recommendation_memory Suggested direction for task memory MiB: -1 reduce, 0 maintain or no_signal, 1 increase.",
        "# TYPE rightsizing_recommendation_memory gauge",
        f"# HELP rightsizing_policy Target CPU%={TARGET_CPU_PCT} mem%={TARGET_MEM_PCT} "
        f"clamp=[{MIN_SCALE},{MAX_SCALE}] min_observed%={MIN_OBS_PCT}",
        "# TYPE rightsizing_policy gauge",
        f'rightsizing_policy{{target_cpu_percent="{TARGET_CPU_PCT}",'
        f'target_memory_percent="{TARGET_MEM_PCT}",min_scale="{MIN_SCALE}",'
        f'max_scale="{MAX_SCALE}",min_observed_percent="{MIN_OBS_PCT}"}} 1',
    ]

    recs: Dict[str, dict] = {}

    if not cpu_data:
        lines.append('rightsizing_cpu_utilization{service="no_data"} 0')
        lines.append('rightsizing_recommended_cpu_scale{service="no_data"} 1')
        lines.append('rightsizing_recommendation_cpu{service="no_data"} 0')
    if not mem_data:
        lines.append('rightsizing_memory_utilization{service="no_data"} 0')
        lines.append('rightsizing_recommended_memory_scale{service="no_data"} 1')
        lines.append('rightsizing_recommendation_memory{service="no_data"} 0')

    for svc, val in cpu_data:
        safe = _escape_label(svc)
        scale, low_sig = recommended_scale(val, TARGET_CPU_PCT)
        code, text = recommendation_from_scale(scale, low_sig)
        lines.append(f'rightsizing_cpu_utilization{{service="{safe}"}} {val}')
        lines.append(f'rightsizing_recommended_cpu_scale{{service="{safe}"}} {scale}')
        lines.append(f'rightsizing_recommendation_cpu{{service="{safe}"}} {code}')
        recs[svc] = {
            "service": svc,
            "cpu_utilization_percent": round(val, 4),
            "recommended_cpu_scale": round(scale, 4),
            "cpu_low_signal": low_sig,
            "cpu_recommendation": text,
            "cpu_recommendation_code": code,
        }

    for svc, val in mem_data:
        safe = _escape_label(svc)
        scale, low_sig = recommended_scale(val, TARGET_MEM_PCT)
        code, text = recommendation_from_scale(scale, low_sig)
        lines.append(f'rightsizing_memory_utilization{{service="{safe}"}} {val}')
        lines.append(f'rightsizing_recommended_memory_scale{{service="{safe}"}} {scale}')
        lines.append(f'rightsizing_recommendation_memory{{service="{safe}"}} {code}')
        if svc not in recs:
            recs[svc] = {"service": svc}
        recs[svc]["memory_utilization_percent"] = round(val, 4)
        recs[svc]["recommended_memory_scale"] = round(scale, 4)
        recs[svc]["memory_low_signal"] = low_sig
        recs[svc]["memory_recommendation"] = text
        recs[svc]["memory_recommendation_code"] = code

    body = "\n".join(lines) + "\n"
    rec_list = list(recs.values())
    return body, rec_list


def _debug_text() -> str:
    lines = [
        f"PROMETHEUS_URL={PROMETHEUS_URL}",
        f"cpu: {LAST_QUERY_INFO.get('cpu', '')}",
        f"memory: {LAST_QUERY_INFO.get('memory', '')}",
        "",
        f"Rightsizing: target_cpu={TARGET_CPU_PCT}% target_mem={TARGET_MEM_PCT}% "
        f"scale_clamp=[{MIN_SCALE},{MAX_SCALE}] min_observed={MIN_OBS_PCT}%",
        "",
        "Recent series (1h) with __name__ matching aws_ecs_.*(cpu|memory).* (RE2) —",
    ]
    series, err = series_match('{__name__=~"aws_ecs_.*(cpu|memory).*"}', 3600)
    if err:
        lines.append(f"series discovery error: {err}")
    else:
        names = sorted(
            {s.get("__name__", "") for s in series if isinstance(s, dict)}
        )
        names = [n for n in names if n]
        if not names:
            lines.append(
                "(none — CloudWatch exporter not scraping ECS, wrong region, or metrics delayed.)"
            )
        else:
            lines.extend(f"  {n}" for n in names[:80])
            if len(names) > 80:
                lines.append(f"  ... and {len(names) - 80} more")
    lines.append("")
    lines.append("Prometheus: Status -> Targets (cw-exporter jobs). Exporter: curl :9106/metrics | grep -i ecs")
    return "\n".join(lines) + "\n"


def _path_key(path: str) -> str:
    return (path or "").split("?", 1)[0].rstrip("/") or "/"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), file=sys.stderr)

    def do_GET(self):
        key = _path_key(self.path)

        if key == "/metrics":
            cpu_data = get_cpu()
            mem_data = get_memory()
            body, _ = build_metrics_and_recommendations(cpu_data, mem_data)
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        elif key == "/recommendations":
            cpu_data = get_cpu()
            mem_data = get_memory()
            _, rec_list = build_metrics_and_recommendations(cpu_data, mem_data)
            payload = {
                "target_cpu_percent": TARGET_CPU_PCT,
                "target_memory_percent": TARGET_MEM_PCT,
                "scale_clamp": {"min": MIN_SCALE, "max": MAX_SCALE},
                "min_observed_percent": MIN_OBS_PCT,
                "recommendation_bands": {
                    "reduce_if_scale_below": REC_DOWN,
                    "increase_if_scale_above": REC_UP,
                    "note": "Derived from recommended_*_scale after clamp; low utilization uses no_signal.",
                },
                "recommendation_codes": {
                    "-1": "reduce",
                    "0": "maintain_or_no_signal",
                    "1": "increase",
                },
                "formula": "recommended_scale = clamp(observed_pct / target_pct, min_scale, max_scale); "
                "if observed < min_observed_percent then scale=1",
                "apply": "new_cpu_units ≈ round(current_cpu_units * recommended_cpu_scale); "
                "new_memory_mib ≈ round(current_memory_mib * recommended_memory_scale)",
                "services": rec_list,
            }
            raw = json.dumps(payload, indent=2) + "\n"
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(raw.encode())

        elif key == "/healthz":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")

        elif key == "/debug":
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(_debug_text().encode())

        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK\n")


def run():
    src = "PROMETHEUS_URL env" if os.environ.get("PROMETHEUS_URL", "").strip() else (
        "default (docker: prometheus:9090)" if os.path.exists("/.dockerenv") else "default (host: 127.0.0.1:9090)"
    )
    print(f"ECS AI Rightsizer — PROMETHEUS_URL={PROMETHEUS_URL} ({src})", file=sys.stderr)
    print(
        f"Rightsizing targets: CPU {TARGET_CPU_PCT}% mem {TARGET_MEM_PCT}% "
        f"scale [{MIN_SCALE},{MAX_SCALE}] min_obs {MIN_OBS_PCT}% "
        f"rec bands scale<{REC_DOWN}→reduce scale>{REC_UP}→increase",
        file=sys.stderr,
    )
    print(
        "Endpoints: /metrics /recommendations /healthz /debug",
        file=sys.stderr,
    )
    _, err = query_promql("vector(1)")
    if err:
        print(f"WARN: Prometheus not reachable: {err}", file=sys.stderr)
    server = HTTPServer(("0.0.0.0", 8000), Handler)
    print("Listening on 0.0.0.0:8000", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    run()
