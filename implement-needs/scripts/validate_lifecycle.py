#!/usr/bin/env python3
"""Validate observable Implement Needs lifecycle ordering and ownership."""
from __future__ import annotations
import argparse, json
from pathlib import Path

RELEASE = ("freeze", "build", "package", "deploy", "smoke")

def evaluate(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"decision":"reject","reasons":[f"unreadable:{exc}" ]}, 1
    events = data.get("events") if isinstance(data, dict) else None
    reasons=[]
    if not isinstance(events, list): return {"decision":"reject","reasons":["events_missing"]},1
    active=set(); archived=set(); release=[]
    for i,e in enumerate(events):
        if not isinstance(e,dict): reasons.append(f"event_{i}_invalid"); continue
        kind=e.get("kind"); owner=e.get("owner"); task=e.get("task")
        if kind=="dispatch_spec":
            if active: reasons.append(f"event_{i}_overlapping_spec")
            if task in archived: reasons.append(f"event_{i}_duplicate_spec")
            active.add(task)
        elif kind=="child_final":
            if task not in active: reasons.append(f"event_{i}_unknown_final")
        elif kind=="verify":
            if task not in active: reasons.append(f"event_{i}_verify_without_active")
        elif kind=="archive":
            if task not in active: reasons.append(f"event_{i}_archive_without_active")
            active.discard(task); archived.add(task)
        elif kind in RELEASE:
            if owner!="controller": reasons.append(f"event_{i}_release_owner")
            if active: reasons.append(f"event_{i}_release_with_active_spec")
            release.append(kind)
        elif kind=="side_question" and e.get("resumed_action")!=e.get("prior_action"):
            reasons.append(f"event_{i}_side_question_drift")
        elif kind=="recover" and e.get("created_duplicate") is not False:
            reasons.append(f"event_{i}_duplicate_recovery")
    if release and release != list(RELEASE[:len(release)]): reasons.append("release_order")
    return ({"decision":"allow","archived_specs":sorted(archived),"release_events":release},0) if not reasons else ({"decision":"reject","reasons":reasons},1)

def main():
    p=argparse.ArgumentParser(); p.add_argument("--events",type=Path,required=True); p.add_argument("--receipt",type=Path)
    a=p.parse_args(); payload,code=evaluate(a.events); raw=json.dumps(payload,ensure_ascii=False,sort_keys=True)+"\n"
    if a.receipt: a.receipt.write_text(raw,encoding="utf-8")
    print(raw,end=""); raise SystemExit(code)
if __name__=="__main__": main()
