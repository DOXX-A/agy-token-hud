#!/usr/bin/env python3
"""
Antigravity Tokens & Quota Extractor
Extracts exact token statistics per conversation directly from local SQLite databases.
Supports Windows native and WSL Linux conversation directories.
Optimized with incremental mtime caching and long-running worker mode for 0% idle CPU.
"""

import os
import sys
import glob
import time
import json
import sqlite3
import tempfile
import shutil
import subprocess
import argparse
from datetime import datetime, timezone

# Global caches for high performance & minimal CPU
_CACHED_CONV_DIRS = None
_LAST_DIR_DISCOVERY_TIME = 0.0

# Per-file cache: db_path -> {"mtime": float, "records": list, "max_ts": int}
_FILE_CACHE = {}
_LAST_METRICS = None
_LAST_METRICS_CONV_ID = None
_LAST_METRICS_SIGNATURE = None
_ACTIVITY_LOGS_CACHE = {}
_LAST_ACTIVITY_LOGS_MTIME = 0.0

def get_running_wsl_distros():
    """Returns currently running WSL distros. Never wakes up sleeping WSL distros."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.check_output(["wsl.exe", "-l", "--running", "-q"], text=False, timeout=1.0)
        try:
            text = out.decode("utf-16le")
        except Exception:
            text = out.decode("utf-8", errors="ignore")
        return [line.strip().replace("\x00", "") for line in text.splitlines() if line.strip().replace("\x00", "")]
    except Exception:
        return []

def get_conversations_dirs(force_refresh=False):
    global _CACHED_CONV_DIRS, _LAST_DIR_DISCOVERY_TIME
    now = time.time()
    if not force_refresh and _CACHED_CONV_DIRS is not None and (now - _LAST_DIR_DISCOVERY_TIME < 300.0):
        # Filter out WSL paths if the distro stopped running
        if sys.platform == "win32" and any(d.startswith(("\\\\wsl.localhost\\", "\\\\wsl$\\")) for d in _CACHED_CONV_DIRS):
            running = set(get_running_wsl_distros())
            valid_dirs = []
            for d in _CACHED_CONV_DIRS:
                if d.startswith(("\\\\wsl.localhost\\", "\\\\wsl$\\")):
                    parts = d.strip("\\").split("\\")
                    distro = parts[1] if len(parts) > 1 else ""
                    if distro in running:
                        valid_dirs.append(d)
                else:
                    valid_dirs.append(d)
            return valid_dirs
        return _CACHED_CONV_DIRS

    dirs = []
    default_dir = os.path.expanduser("~/.gemini/antigravity/conversations")
    if os.path.isdir(default_dir):
        dirs.append(default_dir)

    # If running on Windows, ONLY auto-discover currently running WSL distros (never wake up stopped VMs)
    if sys.platform == "win32":
        try:
            distros = get_running_wsl_distros()
            for d in distros:
                home_path = rf"\\wsl.localhost\{d}\home"
                if os.path.exists(home_path):
                    for u in os.listdir(home_path):
                        cpath = rf"\\wsl.localhost\{d}\home\{u}\.gemini\antigravity\conversations"
                        if os.path.isdir(cpath) and cpath not in dirs:
                            dirs.append(cpath)
        except Exception:
            pass
    elif sys.platform != "win32" and os.path.exists("/mnt/c/Users"):
        # If running in WSL, auto-discover Windows host conversation directories
        try:
            for u in os.listdir("/mnt/c/Users"):
                cpath = os.path.join("/mnt/c/Users", u, ".gemini/antigravity/conversations")
                if os.path.isdir(cpath) and cpath not in dirs:
                    dirs.append(cpath)
        except Exception:
            pass

    _CACHED_CONV_DIRS = dirs
    _LAST_DIR_DISCOVERY_TIME = now
    return dirs

def decode_varint(data, offset):
    res = 0
    shift = 0
    while True:
        if offset >= len(data):
            break
        b = data[offset]
        offset += 1
        res |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    return res, offset

def parse_proto(data, offset=0, end=None):
    if end is None:
        end = len(data)
    fields = []
    while offset < end:
        tag, offset = decode_varint(data, offset)
        field_num = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:
            val, offset = decode_varint(data, offset)
            fields.append((field_num, "varint", val))
        elif wire_type == 2:
            length, offset = decode_varint(data, offset)
            val = data[offset:offset+length]
            offset += length
            fields.append((field_num, "bytes", val))
        elif wire_type == 1:
            val = data[offset:offset+8]
            offset += 8
            fields.append((field_num, "fixed64", val))
        elif wire_type == 5:
            val = data[offset:offset+4]
            offset += 4
            fields.append((field_num, "fixed32", val))
        else:
            break
    return fields

def open_db_readonly(db_path):
    """
    Open SQLite db safely.
    Handles network UNC paths and locked WAL databases by copying to temp if needed.
    """
    if not db_path.startswith("\\\\"):
        try:
            return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.5), None
        except Exception:
            pass

    # Fallback: copy to temp file to bypass network locking / SQLite URI restrictions
    tmp_path = os.path.join(tempfile.gettempdir(), f"agy_hud_{os.path.basename(db_path)}")
    try:
        shutil.copy2(db_path, tmp_path)
        wal = db_path + "-wal"
        if os.path.exists(wal):
            try: shutil.copy2(wal, tmp_path + "-wal")
            except Exception: pass
        shm = db_path + "-shm"
        if os.path.exists(shm):
            try: shutil.copy2(shm, tmp_path + "-shm")
            except Exception: pass
        conn = sqlite3.connect(tmp_path, timeout=0.5)
        return conn, tmp_path
    except Exception:
        return None, None

def get_activity_sessions_meta():
    """
    Safely reads ~/.gemini/antigravity/activity_logs.db in readonly mode to associate each session
    with its active model_name, provider_name and gateway status.
    Uses incremental mtime cache to ensure 0% CPU overhead.
    """
    global _ACTIVITY_LOGS_CACHE, _LAST_ACTIVITY_LOGS_MTIME
    act_path = os.path.expanduser("~/.gemini/antigravity/activity_logs.db")
    if not os.path.exists(act_path):
        return _ACTIVITY_LOGS_CACHE

    try:
        mtime = os.path.getmtime(act_path)
    except OSError:
        return _ACTIVITY_LOGS_CACHE

    if _ACTIVITY_LOGS_CACHE and mtime == _LAST_ACTIVITY_LOGS_MTIME:
        return _ACTIVITY_LOGS_CACHE

    res = {}
    conn, tmp = open_db_readonly(act_path)
    if conn:
        try:
            c = conn.cursor()
            c.execute("""
                SELECT session_id, model_id, provider_name, model_identity_json
                FROM activity_logs
                WHERE session_id IS NOT NULL AND session_id != ''
                GROUP BY session_id
                HAVING timestamp = MAX(timestamp)
            """)
            for sid, model_id, provider_name, meta_json in c.fetchall():
                display_name = model_id
                if meta_json:
                    try:
                        meta = json.loads(meta_json)
                        display_name = meta.get("display_name") or meta.get("provider_model_id") or model_id
                    except Exception:
                        pass
                is_gw = any(k in str(model_id).lower() for k in ["argon", "vertex", "byok"]) or "vertex" in str(provider_name).lower()
                res[sid] = {
                    "model_id": model_id,
                    "model_name": display_name,
                    "provider": provider_name,
                    "is_gateway": is_gw,
                }
        except Exception:
            pass
        finally:
            try: conn.close()
            except Exception: pass
            if tmp and os.path.exists(tmp):
                try: os.remove(tmp)
                except Exception: pass

    _ACTIVITY_LOGS_CACHE = res
    _LAST_ACTIVITY_LOGS_MTIME = mtime
    return res

def parse_db_records(db_path, sess_id):
    """Parse records from a single database file."""
    conn, tmp_path = open_db_readonly(db_path)
    if not conn:
        return [], 0

    records = []
    db_latest_ts = 0
    try:
        c = conn.cursor()
        step_times = {}
        try:
            c.execute("SELECT idx, metadata FROM steps WHERE metadata IS NOT NULL")
            for idx, meta in c.fetchall():
                for fn, wt, val in parse_proto(meta):
                    if fn == 1:
                        for sfn, swt, sval in parse_proto(val):
                            if sfn == 1:
                                step_times[idx] = sval
        except Exception:
            pass
        
        try:
            c.execute("SELECT idx, data FROM gen_metadata ORDER BY idx ASC")
            for idx, data in c.fetchall():
                ts = step_times.get(idx, 0)
                if ts > db_latest_ts:
                    db_latest_ts = ts
                proto = parse_proto(data)
                s9_ctx = 0
                s9_max = 256000
                s17_dict = {}

                for fn, wt, val in proto:
                    if fn == 1:
                        for sfn, swt, sval in parse_proto(val):
                            if sfn == 9 and swt == "bytes":
                                for xfn, xwt, xval in parse_proto(sval):
                                    if xfn == 10 and xwt == "bytes":
                                        sub10 = parse_proto(xval)
                                        d10 = {k: v for k, t, v in sub10 if t == "varint"}
                                        if 1 in d10 and d10[1] > 0:
                                            s9_ctx = d10[1]
                                        if 4 in d10 and d10[4] > 0:
                                            s9_max = d10[4]
                            elif sfn == 17 and swt == "bytes":
                                for tfn, twt, tval in parse_proto(sval):
                                    if twt == "bytes":
                                        sub17 = parse_proto(tval)
                                        d17 = {k: v for k, t, v in sub17 if t == "varint"}
                                        if d17 and (d17.get(2, 0) > 0 or d17.get(3, 0) > 0 or d17.get(5, 0) > 0):
                                            s17_dict.update(d17)

                cached_tok = s17_dict.get(5, 0)
                prompt_tok = s17_dict.get(2, 0)
                output_tok = s17_dict.get(3, 0)
                thinking_tok = s17_dict.get(9, 0)
                text_tok = s17_dict.get(10, 0)

                if s9_ctx > 0:
                    ctx_size = s9_ctx
                    if s17_dict.get(2, 0) > 0:
                        prompt_tok = s17_dict.get(2, 0)
                        if s17_dict.get(5, 0) > 0:
                            cached_tok = s17_dict.get(5, 0)
                        else:
                            cached_tok = max(0, s9_ctx - prompt_tok)
                    else:
                        prompt_tok = s9_ctx
                        cached_tok = 0
                elif cached_tok + prompt_tok > 0:
                    ctx_size = cached_tok + prompt_tok
                else:
                    ctx_size = 0

                real_output_tok = text_tok if text_tok > 0 else (output_tok - thinking_tok if output_tok >= thinking_tok else output_tok)
                effective_max = s9_max if s9_max > 0 else 256000

                if ctx_size > 0 or output_tok > 0:
                    rec = {
                        "session_id": sess_id,
                        "idx": idx,
                        "timestamp": ts,
                        "prompt_tokens": prompt_tok,
                        "output_tokens": real_output_tok,
                        "total_completion_tokens": output_tok,
                        "cached_tokens": cached_tok,
                        "thinking_tokens": thinking_tok,
                        "text_tokens": text_tok,
                        "context_size": ctx_size,
                        "max_context": effective_max,
                    }
                    records.append(rec)
        except Exception:
            pass
        conn.close()
    except Exception:
        try: conn.close()
        except Exception: pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except Exception: pass
            for ext in ["-wal", "-shm"]:
                if os.path.exists(tmp_path + ext):
                    try: os.remove(tmp_path + ext)
                    except Exception: pass

    return records, db_latest_ts

def collect_metrics(current_conv_id=None):
    global _LAST_METRICS, _LAST_METRICS_CONV_ID, _LAST_METRICS_SIGNATURE, _FILE_CACHE

    now_ts = int(time.time())
    db_dirs = get_conversations_dirs()
    db_files = []
    running_distros = None
    for d in db_dirs:
        if sys.platform == "win32" and d.startswith(("\\\\wsl.localhost\\", "\\\\wsl$\\")):
            if running_distros is None:
                running_distros = set(get_running_wsl_distros())
            parts = d.strip("\\").split("\\")
            distro = parts[1] if len(parts) > 1 else ""
            if distro not in running_distros:
                continue
        db_files.extend(glob.glob(os.path.join(d, "*.db")))

    # Extract mtimes for all db files
    file_mtimes = {}
    for f in db_files:
        try:
            file_mtimes[f] = os.path.getmtime(f)
        except OSError:
            pass

    # Sort files by mtime descending
    db_files = sorted(file_mtimes.keys(), key=lambda f: file_mtimes.get(f, 0), reverse=True)
    
    if not current_conv_id and db_files:
        current_conv_id = os.path.basename(db_files[0]).replace(".db", "")

    cutoff_ts = now_ts - (30 * 86400)
    
    # Check if we can reuse the entire metrics payload
    # Generate signature based on recent files' mtimes
    recent_sig = []
    for f in db_files[:30]:
        recent_sig.append((f, file_mtimes.get(f, 0)))

    act_path = os.path.expanduser("~/.gemini/antigravity/activity_logs.db")
    if os.path.exists(act_path):
        try:
            recent_sig.append(("activity_logs", os.path.getmtime(act_path)))
        except OSError:
            pass

    current_sig = tuple(recent_sig)

    if _LAST_METRICS and _LAST_METRICS_CONV_ID == current_conv_id and _LAST_METRICS_SIGNATURE == current_sig:
        # No modification detected, return cached response with updated timestamp
        _LAST_METRICS["updated_at"] = datetime.now(timezone.utc).isoformat()
        return _LAST_METRICS

    all_records = []
    latest_ts = 0

    for db_path in db_files:
        sess_id = os.path.basename(db_path).replace(".db", "")
        mtime = file_mtimes.get(db_path, 0)

        if sess_id != current_conv_id:
            if mtime < cutoff_ts:
                continue

        # Check per-file cache
        cached = _FILE_CACHE.get(db_path)
        if cached and cached["mtime"] == mtime:
            records = cached["records"]
            db_ts = cached["max_ts"]
        else:
            records, db_ts = parse_db_records(db_path, sess_id)
            _FILE_CACHE[db_path] = {
                "mtime": mtime,
                "records": records,
                "max_ts": db_ts
            }

        if db_ts > latest_ts:
            latest_ts = db_ts
        all_records.extend(records)

    ref_ts = latest_ts if latest_ts > 0 else now_ts
    h5_ts = ref_ts - (5 * 3600)
    w1_ts = ref_ts - (7 * 86400)

    h5_records = [r for r in all_records if r["timestamp"] >= h5_ts]
    w1_records = [r for r in all_records if r["timestamp"] >= w1_ts]

    activity_meta = get_activity_sessions_meta()

    sessions_map = {}
    for r in all_records:
        sid = r["session_id"]
        ctx = r["context_size"]
        max_ctx = r.get("max_context", 256000)

        meta = activity_meta.get(sid, {})
        model_name = meta.get("model_name")
        provider = meta.get("provider")
        is_gw = meta.get("is_gateway", False) or (max_ctx != 256000)

        sessions_map[sid] = {
            "session_id": sid,
            "context_size": ctx,
            "max_context": max_ctx,
            "context_percent": round((ctx / max_ctx) * 100, 2) if max_ctx > 0 else 0.0,
            "cached_tokens": r["cached_tokens"],
            "prompt_tokens": r["prompt_tokens"],
            "output_tokens": r["output_tokens"],
            "thinking_tokens": r["thinking_tokens"],
            "text_tokens": r["text_tokens"],
            "model_name": model_name,
            "provider": provider,
            "is_gateway": is_gw,
        }

    current_session = sessions_map.get(current_conv_id)
    current_context = current_session["context_size"] if current_session else 0
    current_max = current_session["max_context"] if current_session else 256000
    current_pct = current_session["context_percent"] if current_session else 0.0
    current_cached = current_session["cached_tokens"] if current_session else 0
    current_prompt = current_session["prompt_tokens"] if current_session else 0
    current_output = current_session["output_tokens"] if current_session else 0
    current_thinking = current_session["thinking_tokens"] if current_session else 0
    current_text = current_session["text_tokens"] if current_session else 0
    current_model = current_session.get("model_name") if current_session else None
    current_provider = current_session.get("provider") if current_session else None
    current_is_gw = current_session.get("is_gateway", False) if current_session else False

    metrics = {
        "current_session": {
            "session_id": current_conv_id,
            "context_size": current_context,
            "max_context": current_max,
            "context_percent": current_pct,
            "cached_tokens": current_cached,
            "prompt_tokens": current_prompt,
            "output_tokens": current_output,
            "thinking_tokens": current_thinking,
            "text_tokens": current_text,
            "model_name": current_model,
            "provider": current_provider,
            "is_gateway": current_is_gw,
        },
        "sessions": sessions_map,
        "usage_5h": {
            "window_hours": 5,
            "total_requests": len(h5_records),
            "input_tokens": sum(r["prompt_tokens"] for r in h5_records),
            "output_tokens": sum(r["output_tokens"] for r in h5_records),
            "thinking_tokens": sum(r["thinking_tokens"] for r in h5_records),
            "total_tokens": sum(r["prompt_tokens"] + r["output_tokens"] for r in h5_records),
        },
        "usage_weekly": {
            "window_days": 7,
            "total_requests": len(w1_records),
            "input_tokens": sum(r["prompt_tokens"] for r in w1_records),
            "output_tokens": sum(r["output_tokens"] for r in w1_records),
            "thinking_tokens": sum(r["thinking_tokens"] for r in w1_records),
            "total_tokens": sum(r["prompt_tokens"] + r["output_tokens"] for r in w1_records),
        },
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    _LAST_METRICS = metrics
    _LAST_METRICS_CONV_ID = current_conv_id
    _LAST_METRICS_SIGNATURE = current_sig

    return metrics

def run_worker():
    """Long-running worker process communicating via newline-delimited JSON on stdio."""
    # Pre-warm directory cache
    get_conversations_dirs()

    # Worker loop
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            action = req.get("action", "collect")
            if action == "collect":
                session_id = req.get("session_id")
                metrics = collect_metrics(session_id)
                sys.stdout.write(json.dumps({"status": "ok", "data": metrics}) + "\n")
                sys.stdout.flush()
            elif action == "ping":
                sys.stdout.write(json.dumps({"status": "ok", "message": "pong"}) + "\n")
                sys.stdout.flush()
            elif action == "exit":
                break
            else:
                sys.stdout.write(json.dumps({"status": "error", "error": f"Unknown action: {action}"}) + "\n")
                sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(json.dumps({"status": "error", "error": str(e)}) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity Tokens Extractor")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--session", type=str, default=None, help="Specific session ID")
    parser.add_argument("--worker", action="store_true", help="Run as persistent worker with stdio IPC")
    args = parser.parse_args()

    if args.worker:
        run_worker()
    else:
        metrics = collect_metrics(args.session)
        if args.json:
            print(json.dumps(metrics, indent=2))
        else:
            print(json.dumps(metrics))
