#!/usr/bin/env python3
"""
Antigravity Tokens & Quota Extractor
Extracts exact token statistics per conversation directly from local SQLite databases.
Supports Windows native and WSL Linux conversation directories.
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

def get_conversations_dirs():
    dirs = []
    default_dir = os.path.expanduser("~/.gemini/antigravity/conversations")
    if os.path.isdir(default_dir):
        dirs.append(default_dir)

    # If running on Windows, also auto-discover WSL distros
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(["wsl.exe", "-l", "-q"], text=False, timeout=2)
            try:
                text = out.decode("utf-16le")
            except Exception:
                text = out.decode("utf-8", errors="ignore")
            distros = [line.strip().replace("\x00", "") for line in text.splitlines() if line.strip().replace("\x00", "")]
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

def collect_metrics(current_conv_id=None):
    now_ts = int(time.time())
    db_dirs = get_conversations_dirs()
    db_files = []
    for d in db_dirs:
        db_files.extend(glob.glob(os.path.join(d, "*.db")))
    db_files.sort(key=os.path.getmtime, reverse=True)
    
    if not current_conv_id and db_files:
        current_conv_id = os.path.basename(db_files[0]).replace(".db", "")

    all_records = []
    latest_ts = 0
    cutoff_ts = now_ts - (30 * 86400)

    for db_path in db_files:
        sess_id = os.path.basename(db_path).replace(".db", "")
        if sess_id != current_conv_id:
            try:
                if os.path.getmtime(db_path) < cutoff_ts:
                    continue
            except OSError:
                pass
        
        conn, tmp_path = open_db_readonly(db_path)
        if not conn:
            continue

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
                    if ts > latest_ts:
                        latest_ts = ts
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

                    if cached_tok + prompt_tok > 0:
                        ctx_size = cached_tok + prompt_tok
                    elif s9_ctx > 0:
                        ctx_size = s9_ctx
                        prompt_tok = s9_ctx
                    else:
                        ctx_size = 0

                    if ctx_size > 0 or output_tok > 0:
                        rec = {
                            "session_id": sess_id,
                            "idx": idx,
                            "timestamp": ts,
                            "prompt_tokens": prompt_tok,
                            "output_tokens": output_tok,
                            "cached_tokens": cached_tok,
                            "thinking_tokens": thinking_tok,
                            "text_tokens": text_tok,
                            "context_size": ctx_size,
                            "max_context": s9_max if s9_max > 0 else 256000,
                        }
                        all_records.append(rec)
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

    ref_ts = latest_ts if latest_ts > 0 else now_ts
    h5_ts = ref_ts - (5 * 3600)
    w1_ts = ref_ts - (7 * 86400)

    h5_records = [r for r in all_records if r["timestamp"] >= h5_ts]
    w1_records = [r for r in all_records if r["timestamp"] >= w1_ts]

    sessions_map = {}
    for r in all_records:
        sid = r["session_id"]
        ctx = r["context_size"]
        max_ctx = r.get("max_context", 256000)
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

    return {
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity Tokens Extractor")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--session", type=str, default=None, help="Specific session ID")
    args = parser.parse_args()

    metrics = collect_metrics(args.session)
    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print(json.dumps(metrics))
