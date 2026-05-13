from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from roku_ecp import query_apps, find_app_id, keypress, launch, youtube_params_from_url, type_text

APP_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "runs.log"

DEFAULT_CONFIG: dict[str, Any] = {
    "roku_ip": "10.0.0.141",

    # schedules (multiple)
    "schedules": [
        {
            "id": "morning",
            "enabled": True,
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "time_hhmm": "07:00",
            "repeat_every_weeks": 1,
            "list": "default",
        }
    ],

    # YouTube launching
    "launch_youtube": True,
    "youtube_app_name": "YouTube",
    "youtube_fallback_app_id": "837",

    # Video lists managed in the UI
    "video_lists": {
        "default": [],  # [{"url": "https://www.youtube.com/watch?v=...", "active": true}]
    },

    # video picking
    "pick_mode": "random_active",  # random_active | first_active
}


def log(msg: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_PATH.write_text((LOG_PATH.read_text("utf-8") if LOG_PATH.exists() else "") + f"[{ts}] {msg}\n", "utf-8")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg or {})
        # migrate legacy "videos" into video_lists.default if present
        if isinstance(merged.get("videos"), list) and merged.get("videos") and not merged.get("video_lists", {}).get("default"):
            merged.setdefault("video_lists", {}).setdefault("default", [])
            merged["video_lists"]["default"] = merged["videos"]
        merged.pop("videos", None)

        # normalize video_lists
        vls = merged.get("video_lists")
        if not isinstance(vls, dict):
            merged["video_lists"] = {"default": []}
        merged.setdefault("video_lists", {}).setdefault("default", [])

        # normalize schedules (and migrate legacy single schedule fields)
        schedules = merged.get("schedules")
        if not isinstance(schedules, list) or not schedules:
            schedules = []

        # legacy fields: enabled/days/time_hhmm/repeat/selected_list
        if any(k in merged for k in ("enabled", "days", "time_hhmm", "repeat_every_weeks", "selected_list")) and not schedules:
            schedules = [
                {
                    "id": "schedule1",
                    "enabled": bool(merged.get("enabled", True)),
                    "days": merged.get("days") or ["mon"],
                    "time_hhmm": merged.get("time_hhmm") or "07:00",
                    "repeat_every_weeks": int(merged.get("repeat_every_weeks") or 1),
                    "list": merged.get("selected_list") or "default",
                }
            ]

        # cleanup legacy keys
        for k in ("enabled", "days", "time_hhmm", "repeat_every_weeks", "selected_list"):
            merged.pop(k, None)

        # validate schedule objects
        out = []
        seen = set()
        for s in schedules:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "").strip() or f"sched_{len(out)+1}"
            if sid in seen:
                continue
            seen.add(sid)
            lst = s.get("list") or "default"
            if lst not in merged["video_lists"]:
                lst = "default"
            out.append(
                {
                    "id": sid,
                    "enabled": bool(s.get("enabled", True)),
                    "days": [d for d in (s.get("days") or []) if d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
                    "time_hhmm": str(s.get("time_hhmm") or "07:00"),
                    "repeat_every_weeks": int(s.get("repeat_every_weeks") or 1),
                    "list": lst,
                }
            )
        merged["schedules"] = out

        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), "utf-8")


scheduler = BackgroundScheduler(timezone=os.environ.get("TZ", "America/Denver"))
scheduler.start()

def schedule_jobs(cfg: dict) -> None:
    # clear existing jobs
    for j in list(scheduler.get_jobs()):
        try:
            scheduler.remove_job(j.id)
        except Exception:
            pass

    schedules = cfg.get("schedules") or []
    if not schedules:
        log("No schedules configured")
        return

    for s in schedules:
        if not s.get("enabled"):
            continue

        sid = str(s.get("id"))
        hhmm = str(s.get("time_hhmm") or "07:00")
        if ":" not in hhmm:
            log(f"Invalid time for schedule {sid}: {hhmm}")
            continue
        hour_s, min_s = hhmm.split(":", 1)
        hour = int(hour_s)
        minute = int(min_s)

        days = s.get("days") or []
        if not days:
            log(f"Schedule {sid} has no days; skipping")
            continue

        dow = ",".join(days)
        repeat_weeks = int(s.get("repeat_every_weeks") or 1)
        repeat_weeks = max(1, min(12, repeat_weeks))

        trigger = CronTrigger(day_of_week=dow, hour=hour, minute=minute)

        def wrapped(schedule_id=sid):
            schedule = next((x for x in (cfg.get("schedules") or []) if x.get("id") == schedule_id), None)
            if not schedule:
                return
            if repeat_weeks > 1:
                iso_week = int(datetime.now().strftime("%V"))
                if iso_week % repeat_weeks != 0:
                    return
            run_roku(cfg, schedule)

        scheduler.add_job(wrapped, trigger=trigger, id=f"roku_{sid}", replace_existing=True)
        log(f"Scheduled[{sid}]: days={days} time={hhmm} repeat_every_weeks={repeat_weeks} list={s.get('list')}")


def run_roku(cfg: dict, schedule: dict | None = None) -> None:
    roku_ip = cfg.get("roku_ip")
    if not roku_ip:
        log("ERROR: missing roku_ip")
        return

    schedule = schedule or {"id": "manual", "list": "default"}
    list_name = schedule.get("list") or "default"

    try:
        if cfg.get("launch_youtube"):
            apps = query_apps(roku_ip)
            app_id = find_app_id(apps, cfg.get("youtube_app_name", "YouTube")) or cfg.get(
                "youtube_fallback_app_id", "837"
            )

            video_lists = cfg.get("video_lists") or {}
            videos = video_lists.get(list_name) or []
            active = [v for v in videos if (v or {}).get("active") and (v or {}).get("url")]
            pick_mode = (cfg.get("pick_mode") or "random_active").strip()

            chosen_url = None
            if pick_mode == "first_active":
                chosen_url = (active[0]["url"] if active else None)
            else:
                chosen_url = (random.choice(active)["url"] if active else None)

            if not chosen_url:
                log(f"ERROR: no active videos configured for list={list_name}")
                return

            params = youtube_params_from_url(chosen_url)
            if not params:
                log(f"ERROR: could not extract video id from URL: {chosen_url}")
                return

            launch(roku_ip, str(app_id), params=params)
            log(f"OK: launched YouTube video (schedule={schedule.get('id')} list={list_name}): {chosen_url}")
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")


app = FastAPI(title="Roku Scheduler")


@app.on_event("startup")
def _startup():
    cfg = load_config()
    try:
        schedule_jobs(cfg)
    except Exception as e:
        log(f"ERROR scheduling on startup: {e}")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    cfg = load_config()
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "cfg": cfg,
            "log_tail": (LOG_PATH.read_text("utf-8").splitlines()[-40:] if LOG_PATH.exists() else []),
            "days_all": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        },
    )


@app.post("/add-schedule")
def add_schedule(
    schedule_id: str = Form(...),
    time_hhmm: str = Form(...),
    repeat_every_weeks: int = Form(1),
    days: list[str] = Form(default=[]),
    enabled: str = Form(default="on"),
    list_name: str = Form(default="default"),
):
    cfg = load_config()
    sid = schedule_id.strip()
    if not sid:
        raise HTTPException(400, "schedule_id required")
    if any(s.get("id") == sid for s in (cfg.get("schedules") or [])):
        raise HTTPException(400, "schedule_id already exists")

    vls = cfg.get("video_lists") or {}
    if list_name not in vls:
        list_name = "default"

    sched = {
        "id": sid,
        "enabled": (enabled == "on"),
        "days": [d for d in days if d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
        "time_hhmm": time_hhmm.strip(),
        "repeat_every_weeks": int(repeat_every_weeks),
        "list": list_name,
    }
    cfg.setdefault("schedules", []).append(sched)
    save_config(cfg)
    schedule_jobs(cfg)
    return RedirectResponse(url="/", status_code=303)


@app.post("/save-videos")
def save_videos(
    list_name: str = Form(default="default"),
    new_list_name: str = Form(default=""),
    pick_mode: str = Form(default="random_active"),
    videos_text: str = Form(default=""),
):
    cfg = load_config()

    pick_mode = (pick_mode or "random_active").strip()
    if pick_mode not in ("random_active", "first_active"):
        pick_mode = "random_active"
    cfg["pick_mode"] = pick_mode

    vls = cfg.get("video_lists") or {}
    if not isinstance(vls, dict):
        vls = {"default": []}

    nl = (new_list_name or "").strip()
    if nl:
        vls.setdefault(nl, [])
        list_name = nl

    if list_name not in vls:
        list_name = "default"

    videos = []
    for raw in (videos_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        active = True
        if line.startswith("#"):
            active = False
            line = line.lstrip("#").strip()
        if line:
            videos.append({"url": line, "active": active})
    vls[list_name] = videos

    cfg["video_lists"] = vls
    cfg["selected_list"] = list_name

    save_config(cfg)
    return RedirectResponse(url="/", status_code=303)


@app.post("/delete-schedule")
def delete_schedule(schedule_id: str = Form(...)):
    cfg = load_config()
    cfg["schedules"] = [s for s in (cfg.get("schedules") or []) if s.get("id") != schedule_id]
    save_config(cfg)
    schedule_jobs(cfg)
    return RedirectResponse(url="/", status_code=303)


@app.post("/run-now")
def run_now(list_name: str = Form(default="default")):
    cfg = load_config()
    # run using selected list or provided list_name
    vls = cfg.get("video_lists") or {}
    if list_name not in vls:
        list_name = "default"
    run_roku(cfg, {"id": "manual", "list": list_name})
    return RedirectResponse(url="/", status_code=303)
