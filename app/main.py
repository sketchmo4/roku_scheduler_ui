from __future__ import annotations

import json
import os
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

from roku_ecp import query_apps, find_app_id, keypress, launch, youtube_params_from_url

APP_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "runs.log"

DEFAULT_CONFIG: dict[str, Any] = {
    "roku_ip": "10.0.0.141",
    "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "time_hhmm": "07:00",
    "repeat_every_weeks": 1,
    "press_home_first": True,
    "launch_youtube": True,
    "youtube_app_name": "YouTube",
    "youtube_fallback_app_id": "837",
    "youtube_url": "",
    "enabled": False,
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
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), "utf-8")


scheduler = BackgroundScheduler(timezone=os.environ.get("TZ", "America/Denver"))
scheduler.start()

CURRENT_JOB_ID = "roku_job"


def schedule_job(cfg: dict) -> None:
    # remove old
    try:
        scheduler.remove_job(CURRENT_JOB_ID)
    except Exception:
        pass

    if not cfg.get("enabled"):
        log("Scheduler disabled; no job scheduled")
        return

    # parse hh:mm
    hhmm = str(cfg.get("time_hhmm") or "07:00")
    if ":" not in hhmm:
        raise ValueError("time_hhmm must be HH:MM")
    hour_s, min_s = hhmm.split(":", 1)
    hour = int(hour_s)
    minute = int(min_s)

    days = cfg.get("days") or []
    if not days:
        raise ValueError("Choose at least one day")

    # APScheduler Cron: day_of_week accepts mon,tue,...
    dow = ",".join(days)

    # weekly repeat: simplest is schedule every week and gate by week number modulus.
    repeat_weeks = int(cfg.get("repeat_every_weeks") or 1)
    repeat_weeks = max(1, min(12, repeat_weeks))

    trigger = CronTrigger(day_of_week=dow, hour=hour, minute=minute)

    def wrapped():
        if repeat_weeks > 1:
            iso_week = int(datetime.now().strftime("%V"))
            if iso_week % repeat_weeks != 0:
                return
        run_roku(cfg)

    scheduler.add_job(wrapped, trigger=trigger, id=CURRENT_JOB_ID, replace_existing=True)
    log(f"Scheduled: days={days} time={hhmm} repeat_every_weeks={repeat_weeks}")


def run_roku(cfg: dict) -> None:
    roku_ip = cfg.get("roku_ip")
    if not roku_ip:
        log("ERROR: missing roku_ip")
        return

    try:
        if cfg.get("press_home_first"):
            keypress(roku_ip, "Home")
            time.sleep(0.6)

        if cfg.get("launch_youtube"):
            apps = query_apps(roku_ip)
            app_id = find_app_id(apps, cfg.get("youtube_app_name", "YouTube")) or cfg.get(
                "youtube_fallback_app_id", "837"
            )
            yt_url = (cfg.get("youtube_url") or "").strip()
            params = youtube_params_from_url(yt_url) if yt_url else None
            launch(roku_ip, str(app_id), params=params)

        log(f"OK: launched YouTube on {roku_ip}")
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")


app = FastAPI(title="Roku Scheduler")


@app.on_event("startup")
def _startup():
    cfg = load_config()
    try:
        schedule_job(cfg)
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


@app.post("/save")
def save(
    roku_ip: str = Form(...),
    time_hhmm: str = Form(...),
    repeat_every_weeks: int = Form(1),
    enabled: str | None = Form(default=None),
    press_home_first: str | None = Form(default=None),
    days: list[str] = Form(default=[]),
    youtube_url: str = Form(default=""),
):
    cfg = load_config()
    cfg["roku_ip"] = roku_ip.strip()
    cfg["time_hhmm"] = time_hhmm.strip()
    cfg["repeat_every_weeks"] = int(repeat_every_weeks)
    cfg["days"] = [d for d in days if d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
    cfg["enabled"] = bool(enabled)
    cfg["press_home_first"] = bool(press_home_first)
    cfg["youtube_url"] = youtube_url.strip()

    save_config(cfg)
    try:
        schedule_job(cfg)
    except Exception as e:
        raise HTTPException(400, str(e))

    return RedirectResponse(url="/", status_code=303)


@app.post("/run-now")
def run_now():
    cfg = load_config()
    run_roku(cfg)
    return RedirectResponse(url="/", status_code=303)
