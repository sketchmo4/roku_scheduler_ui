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


def type_text(roku_ip: str, text: str) -> None:
    # Roku ECP supports /keypress/Lit_<text> (URL-encoded)
    for ch in text:
        enc = urllib.parse.quote(ch, safe="")
        http_post(f"http://{roku_ip}:8060/keypress/Lit_{enc}")


def launch(roku_ip: str, app_id: str, params: dict | None = None) -> None:
    url = f"http://{roku_ip}:8060/launch/{app_id}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    http_post(url)


def youtube_video_id_from_url(url: str) -> str | None:
    try:
        u = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(u.query)
        vid = (qs.get("v") or [""])[0].strip()
        if vid:
            return vid
        # youtu.be/<id>
        if u.netloc.endswith("youtu.be"):
            p = u.path.strip("/")
            return p or None
    except Exception:
        return None
    return None


def youtube_params_from_url(url: str) -> dict:
    """Best-effort YouTube deep link params (video id only).

    We keep this intentionally simple: launch YouTube on a specific video id.
    """
    vid = youtube_video_id_from_url(url)
    if vid:
        return {"contentID": vid, "mediaType": "video"}
    return {}
