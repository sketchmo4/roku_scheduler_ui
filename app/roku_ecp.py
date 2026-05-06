from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def http_get(url: str, timeout: float = 4.0) -> bytes:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post(url: str, timeout: float = 4.0) -> None:
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def query_apps(roku_ip: str) -> list[dict]:
    xml = http_get(f"http://{roku_ip}:8060/query/apps")
    root = ET.fromstring(xml)
    out = []
    for app in root.findall("./app"):
        out.append({"id": app.attrib.get("id"), "name": (app.text or "").strip()})
    return out


def find_app_id(apps: list[dict], name: str) -> str | None:
    name_l = name.strip().lower()
    for a in apps:
        if (a.get("name") or "").strip().lower() == name_l:
            return a.get("id")
    for a in apps:
        if name_l in ((a.get("name") or "").strip().lower()):
            return a.get("id")
    return None


def keypress(roku_ip: str, key: str) -> None:
    http_post(f"http://{roku_ip}:8060/keypress/{key}")


def launch(roku_ip: str, app_id: str, params: dict | None = None) -> None:
    url = f"http://{roku_ip}:8060/launch/{app_id}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    http_post(url)


def youtube_params_from_url(url: str) -> dict:
    """Best-effort YouTube deep link params.

    Notes:
    - Roku YouTube deep-linking is not consistently documented.
    - We try a few common conventions; if it doesn't work, we still at least launch YouTube.
    """
    try:
        u = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(u.query)
        # playlist id
        playlist = (qs.get("list") or [""])[0]
        video = (qs.get("v") or [""])[0]
        if playlist and video:
            return {"contentID": video, "mediaType": "video", "list": playlist}
        if playlist:
            # treat playlist as 'list' param
            return {"list": playlist}
        if video:
            return {"contentID": video, "mediaType": "video"}
    except Exception:
        pass
    return {}
