#!/usr/bin/env python3
"""
北斗智影 AI 创作者中心 CLI

已支持:
- user: 用户信息
- credit: 积分余额
- products: AI 工具/产品列表
- languages: 翻译语言
- publish: 矩阵发布
- uploads: 媒资库管理
- analyze: 智影解析
- clip: 智能剪辑
- translate: 视频翻译
- manus: 我的作品
- list: 短剧列表
- detail: 短剧详情/推广链接
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
from websocket import create_connection
from websocket._exceptions import WebSocketException, WebSocketTimeoutException


SCENTER_API = "https://api-scenter.inbeidou.cn/agent/v1"
ICENTER_API = "https://api-icenter.inbeidou.cn/ai/v1"
TOOL_API = "https://api-tool.inbeidou.cn/ai/v1"
WS_MANUS_CHATS = "wss://api-icenter.inbeidou.cn/ai/v1/ws/manus/chats"

DEFAULT_TIMEOUT = 60
DEFAULT_POLL_INTERVAL = 3
DEFAULT_TASK_TIMEOUT = 1800
STATE_FILE = Path.home() / ".inbeidou_cli_state.json"
AUTH_STATE_FILE = Path.home() / ".barry-video" / "auth_state.json"
DEFAULT_DRAMA_ASSET_BASE_URL = "https://play.inbeidou.cn"

PLATFORMS = {
    "dramabox": "DramaBox",
    "flareflow": "FlareFlow",
    "shortmax": "ShortMax",
    "flickreels": "FlickReels",
    "reelshort": "ReelShort",
    "goodshort": "GoodShort",
    "moboreels": "MoboReels",
    "kalos": "KalosTV",
    "snackshort": "SnackShort",
    "touchshort": "TouchShort",
    "dreameshort": "DreameShort",
    "honeyreels": "HoneyReels",
    "pancake": "Pancake",
    "starshort": "StarShort",
    "sereal": "Sereal+",
    "dramasnacker": "DramaSnacker(H5)",
    "playlet": "Playlet",
}

PROMOTION_PLATFORMS = {
    1: "TikTok",
    2: "Facebook",
    3: "Instagram",
    4: "YouTube",
}
PROMOTION_PLATFORM_NAMES = {name.lower(): platform_id for platform_id, name in PROMOTION_PLATFORMS.items()}

HIGH_CUT_TASK_KEY = "high"
TRANSLATE_TASK_KEY = "trans"
RUNNING_STATUSES = {"loading", "pending", "processing", "executing"}
HIGH_CUT_CHOICES = ["high_cut", "high_mixed", "golden_three", "golden_clips", "high_pre"]
DEDUPLICATION_CHOICES = [
    "common_deduplication",
    "apply_pip",
    "apply_rotate",
    "apply_scale",
    "apply_flip",
    "apply_frame",
    "apply_special",
    "apply_speed",
    "apply_reduce_frame_rate",
    "apply_mirror_pip",
]
DEFAULT_DEDUPLICATION = ["common_deduplication", "apply_pip"]

DEFAULT_TRANSLATE_CONFIG = {
    "source_language": "zh",
    "target_language": "en",
    "need_speech_translate": True,
    "subtitle_type": "double",
    "subtitle_y": 60,
    "font": "Alibaba PuHuiTi",
    "font_size": 22,
    "font_color": "#ffffff",
    "alignment": "Center",
    "font_face_bold": False,
    "font_face_underline": False,
    "font_face_italic": False,
    "font_color_opacity": 100,
    "effect_color_style": "",
    "shadow": False,
    "shadow_shift": 3,
    "shadow_x_bord": 1,
    "shadow_y_bord": 1,
    "shadow_opacity": 80,
    "outline": False,
    "outline_board": 3,
}

DEFAULT_HIGH_CUT_CONFIG = {
    "cut_duration": "auto",
    "output_count": 1,
    "cut_type": "high_cut",
    "script_count": 1,
    "watermark": "",
}

PUBLISH_SOCIAL_TYPES = ["TIKTOK", "FACEBOOK", "INSTAGRAM", "YOUTUBE"]
PUBLISH_SOCIAL_NAMES = {
    "TIKTOK": "TikTok",
    "FACEBOOK": "Facebook",
    "INSTAGRAM": "Instagram",
    "YOUTUBE": "YouTube",
}
PUBLISH_ACCOUNT_STATUSES = {
    0: "正常授权中",
    1: "授权已失效",
    2: "未绑定公共主页/频道",
}
PUBLISH_POST_STATUS_VALUE = {
    "published": 0,
    "scheduled": 1,
}
PUBLISH_MAX_UPLOAD_SIZE = 1000 * 1024 * 1024


class InbeidouError(RuntimeError):
    """通用 CLI 异常。"""


def save_state(data):
    """保存最近一次上传/任务上下文，便于后续命令复用。"""
    payload = {}
    if STATE_FILE.exists():
        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload.update(data)
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state():
    """读取最近一次上下文。"""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_auth_token():
    """优先从环境变量，其次从 beidou-auth 缓存读取 token。"""
    token = os.getenv("INBEIDOU_TOKEN", "").strip()
    if token:
        return token

    if not AUTH_STATE_FILE.exists():
        return ""

    try:
        payload = json.loads(AUTH_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""

    if payload.get("status") != "success":
        return ""
    if not payload.get("expired_at") or int(payload["expired_at"]) <= int(time.time() * 1000):
        return ""
    return str(payload.get("access_token", "")).strip()


def auth_headers(auth_style="raw"):
    """按站点生成鉴权头。"""
    token = load_auth_token()
    if not token:
        raise InbeidouError("缺少 TOKEN，请设置 INBEIDOU_TOKEN 或完成 ~/.barry-video/auth_state.json 授权")
    if auth_style == "bearer":
        token = f"Bearer {token}"
    return {"Authorization": token}


def api_request(
    base_url,
    path,
    method="GET",
    params=None,
    json_data=None,
    data=None,
    files=None,
    auth_style="raw",
    timeout=DEFAULT_TIMEOUT,
):
    """统一 HTTP 请求。"""
    url = f"{base_url}{path}"
    headers = auth_headers(auth_style=auth_style)
    if json_data is not None:
        headers["Content-Type"] = "application/json"

    try:
        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=json_data,
            data=data,
            files=files,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise InbeidouError(f"请求失败: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise InbeidouError(
            f"接口返回非 JSON: HTTP {response.status_code}, body={response.text[:300]}"
        ) from exc

    if response.status_code >= 400:
        raise InbeidouError(
            f"接口请求失败: HTTP {response.status_code}, code={payload.get('code')}, msg={payload.get('msg')}"
        )
    return payload


def require_success(result, action):
    """校验接口返回 code=0。"""
    if result.get("code") != 0:
        raise InbeidouError(f"{action}失败: {result.get('msg')}")
    return result.get("body")


def pretty_print_json(data):
    """输出 JSON。"""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def format_size(size):
    """格式化文件大小。"""
    size = int(size or 0)
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    return f"{size / 1024 / 1024 / 1024:.2f}GB"


def format_seconds(seconds):
    """秒数转 mm:ss / hh:mm:ss。"""
    seconds = int(round(float(seconds or 0)))
    hour, rem = divmod(seconds, 3600)
    minute, second = divmod(rem, 60)
    if hour:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return f"{minute:02d}:{second:02d}"


def format_drama(item):
    """格式化短剧信息。"""
    is_hot = "🔥" if item.get("tag") == "hot" or item.get("hot_content") else ""
    is_new = "🆕" if "最新" in str(item.get("hot_content", "")) else ""
    platform_name = PLATFORMS.get(str(item.get("app_id", "")), f"平台{item.get('app_id', '')}")
    lang = "英文" if str(item.get("language")) == "2" else f"语言{item.get('language', '')}"

    print(f"\n{'=' * 60}")
    print(f"📺 {item.get('title', '未知标题')}")
    print(f"{'=' * 60}")
    print(f"   英文名: {item.get('third_serial_id', 'N/A')}")
    print(f"   平台: {platform_name} {is_hot}{is_new}")
    print(f"   语言: {lang}")
    print(f"   集数: {item.get('episode_count', 0)} 集")
    print(f"   推广人数: {item.get('promoter_number', 0)} 人")
    print(f"   分佣比例: {item.get('share_rate', 0)}%")
    print(f"   发布时间: {item.get('publish_at', 'N/A')}")
    print(f"   任务ID: {item.get('task_id', 'N/A')}")


def normalize_promotion_platform(value):
    raw = str(value).strip()
    if not raw:
        raise InbeidouError("推广平台不能为空")
    if raw.isdigit():
        platform_id = int(raw)
    else:
        platform_id = PROMOTION_PLATFORM_NAMES.get(raw.lower())
    if platform_id not in PROMOTION_PLATFORMS:
        choices = ", ".join(f"{platform_id}:{name}" for platform_id, name in PROMOTION_PLATFORMS.items())
        raise InbeidouError(f"不支持的推广平台: {value}，可选 {choices}")
    return platform_id


def normalize_promotion_platforms(values, include_all=False):
    if include_all or not values:
        return list(PROMOTION_PLATFORMS.keys())
    ordered = []
    seen = set()
    for value in values:
        platform_id = normalize_promotion_platform(value)
        if platform_id not in seen:
            seen.add(platform_id)
            ordered.append(platform_id)
    return ordered


def resolve_task_for_detail(args):
    if args.task_id:
        body = require_success(
            get_task_info(task_id=args.task_id, app_id=args.platform, task_type=args.task_type),
            "获取短剧详情",
        )
        return body

    if not args.search:
        raise InbeidouError("detail 至少需要 --task-id 或 --search")

    body = require_success(
        get_tasks(
            page=1,
            page_size=max(1, args.size),
            platform=args.platform,
            language=args.language,
            search=args.search,
            order=args.order,
        ),
        "搜索短剧",
    )
    items = body.get("data", [])
    if not items:
        raise InbeidouError(f"未找到短剧: {args.search}")

    keyword = args.search.strip().lower()
    exact = next((item for item in items if str(item.get("title", "")).strip().lower() == keyword), None)
    return exact or items[0]


def build_promotion_link_entry(platform_id, payload):
    codes = payload.get("codes", []) if isinstance(payload.get("codes"), list) else []
    return {
        "platform_id": platform_id,
        "platform_name": PROMOTION_PLATFORMS.get(platform_id, f"平台{platform_id}"),
        "atr_id": payload.get("atr_id"),
        "app_link": payload.get("app_link", ""),
        "serial_link": payload.get("serial_link", ""),
        "tiktok_dramago_link": payload.get("tiktok_dramago_link", ""),
        "tiktok_url": payload.get("tiktok_url", ""),
        "code": payload.get("code", ""),
        "promote_code_content": payload.get("promote_code_content", ""),
        "codes": codes,
    }


def normalize_asset_url(value):
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        return f"{DEFAULT_DRAMA_ASSET_BASE_URL}{value}"
    return value


def iter_nested_items(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield next_path, item
            yield from iter_nested_items(item, next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            next_path = f"{path}[{index}]"
            yield next_path, item
            yield from iter_nested_items(item, next_path)


def collect_urls(value):
    urls = []
    for path, item in iter_nested_items(value):
        if not isinstance(item, str):
            continue
        for match in re.findall(r"https?://[^\s\"'<>，。)）]+", item):
            urls.append({"path": path, "url": match.rstrip(".,")})
    return urls


def pick_url(value, key_hints, extensions):
    candidates = collect_urls(value)
    scored = []
    for candidate in candidates:
        url = candidate["url"]
        parsed_path = urlparse(url).path.lower()
        path = candidate["path"].lower()
        score = 0
        if any(hint in path for hint in key_hints):
            score += 10
        if any(parsed_path.endswith(ext) for ext in extensions):
            score += 5
        if score:
            scored.append((score, candidate))
    if not scored:
        return ""
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]["url"]


def pick_cover_url(item):
    for key in ("third_cover", "cover_url", "cover", "poster", "poster_url", "image", "image_url"):
        value = item.get(key) if isinstance(item, dict) else None
        normalized = normalize_asset_url(value)
        if normalized:
            return normalized
    return pick_url(item, ["cover", "poster", "image", "thumb"], [".jpg", ".jpeg", ".png", ".webp"])


def pick_video_url(item):
    for key in ("play_url", "video_url", "media_url", "preview_url", "trailer_url", "url"):
        value = item.get(key) if isinstance(item, dict) else None
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return pick_url(item, ["video", "media", "preview", "trailer", "play", "playlist"], [".mp4", ".mov", ".m4v", ".webm"])


def probe_video(file_path):
    """用 ffprobe 读取上传所需的视频元数据。"""
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise InbeidouError(f"文件不存在: {path}")
    if not path.is_file():
        raise InbeidouError(f"不是有效文件: {path}")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=width,height:format=duration,size",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise InbeidouError("系统未安装 ffprobe，无法探测视频元数据") from exc
    except subprocess.CalledProcessError as exc:
        raise InbeidouError(f"ffprobe 执行失败: {exc.stderr.strip()}") from exc

    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise InbeidouError("ffprobe 输出解析失败") from exc

    streams = payload.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("width") and stream.get("height")), None)
    if not video_stream:
        raise InbeidouError("未找到视频流，无法上传")

    width = int(video_stream.get("width"))
    height = int(video_stream.get("height"))
    duration_raw = float(payload.get("format", {}).get("duration") or 0)
    file_size = int(payload.get("format", {}).get("size") or path.stat().st_size)

    if height > width:
        orientation = "vertical"
    elif width > height:
        orientation = "horizontal"
    else:
        orientation = "square"

    return {
        "path": str(path),
        "filename": path.name,
        "screen_x": width,
        "screen_y": height,
        "file_size": file_size,
        "file_duration": max(1, math.ceil(duration_raw)),
        "orientation": orientation,
    }


def get_user_info():
    return api_request(SCENTER_API, "/user/info", auth_style="bearer")


def get_credit():
    return api_request(SCENTER_API, "/credit/total", auth_style="bearer")


def get_products():
    return api_request(ICENTER_API, "/product/list")


def get_translation_languages():
    return api_request(ICENTER_API, "/translation/languages")


def get_translation_fonts():
    return api_request(ICENTER_API, "/translation/fonts")


def get_translation_effect_styles():
    return api_request(ICENTER_API, "/translation/effect_color_styles")


def get_tasks(page=1, page_size=15, platform="", language="2", search="", order="publish_at"):
    params = {
        "task_type": "1",
        "page_num": page,
        "page_size": page_size,
        "order_field": order,
        "order_dir": "desc",
        "language": language,
        "search_title": search,
        "agent_id": "2057205410",
    }
    if platform:
        params["app_id"] = platform
    return api_request(SCENTER_API, "/task/page", params=params, auth_style="bearer")


def get_task_info(task_id, app_id="", task_type="1"):
    params = {"task_id": task_id}
    if app_id:
        params["app_id"] = app_id
    if task_type:
        params["task_type"] = task_type
    return api_request(SCENTER_API, "/task/info", params=params, auth_style="bearer")


def get_episode_info(serial_id, episode_order=1, app_id="", task_type="1", need_play=1):
    params = {
        "serial_id": serial_id,
        "episode_order": episode_order,
        "need_play": need_play,
    }
    if app_id:
        params["app_id"] = app_id
    if task_type:
        params["task_type"] = task_type
    return api_request(SCENTER_API, "/episode/info", params=params, auth_style="bearer")


def receive_task(task_id, task_type="1", platform=2):
    """复用 creator task-detail 页点击推广平台按钮时的真实接口。"""
    payload = {
        "task_id": int(task_id),
        "task_type": int(task_type),
        "platform": int(platform),
    }
    return api_request(SCENTER_API, "/task/receive", method="POST", json_data=payload, auth_style="bearer")


def get_uploads(page=1, page_size=10):
    return api_request(ICENTER_API, "/uploads", params={"page_num": page, "page_size": page_size})


def delete_upload(file_id):
    return api_request(ICENTER_API, f"/uploads/{file_id}", method="DELETE")


def get_manus(page=1, page_size=40, source="manus", task_name=""):
    params = {
        "page_num": page,
        "page_size": page_size,
        "source": source,
        "task_name": task_name,
    }
    return api_request(ICENTER_API, "/manus", params=params)


def get_manus_detail(manus_id):
    return api_request(ICENTER_API, f"/manus/{manus_id}")


def delete_manus(manus_id):
    return api_request(ICENTER_API, "/manus/delete", method="POST", json_data={"manus_ids": [int(manus_id)]})


def get_clip_types():
    return api_request(ICENTER_API, "/mp/enum")


def get_publish_accounts():
    return api_request(ICENTER_API, "/publish/team/social", auth_style="bearer")


def upload_publish_file(file_path):
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise InbeidouError(f"文件不存在: {path}")
    if not path.is_file():
        raise InbeidouError(f"不是有效文件: {path}")
    if path.stat().st_size > PUBLISH_MAX_UPLOAD_SIZE:
        raise InbeidouError("发布视频大小不能超过 1000MB")

    with open(path, "rb") as handle:
        result = api_request(
            ICENTER_API,
            "/publish/team/upload",
            method="POST",
            files={"file": (path.name, handle, "video/mp4")},
            auth_style="bearer",
            timeout=120,
        )
    body = require_success(result, "上传发布视频")
    context = {
        "publish_local_file": str(path),
        "publish_file_url": body.get("url"),
        "publish_upload_ext": body.get("ext"),
        "publish_upload_mime": body.get("mime"),
        "publish_upload_size": body.get("file_size"),
    }
    save_state(context)
    return context


def get_publish_records(
    page=1,
    page_size=10,
    post_status=None,
    status="",
    social_type="",
    social_id="",
    start_date="",
    end_date="",
):
    params = {
        "page_num": page,
        "page_size": page_size,
    }
    if post_status is not None:
        params["post_status"] = int(post_status)
    if status:
        params["status"] = status
    if social_type:
        params["type"] = normalize_publish_platform(social_type)
    if social_id:
        params["social_id"] = social_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    return api_request(ICENTER_API, "/publish/team/post", params=params, auth_style="bearer")


def create_publish_post(payload):
    return api_request(
        ICENTER_API,
        "/publish/team/post",
        method="POST",
        json_data=payload,
        auth_style="bearer",
    )


def delete_publish_post(post_id="", team_id="", task_id=""):
    return api_request(
        ICENTER_API,
        "/publish/team/post",
        method="DELETE",
        params={"post_id": post_id, "team_id": team_id, "task_id": task_id},
        auth_style="bearer",
    )


def upload_raw_media(file_path):
    """上传媒资原文件到 api-tool。"""
    video = probe_video(file_path)
    with open(video["path"], "rb") as handle:
        result = api_request(
            TOOL_API,
            "/media/upload",
            method="POST",
            data={
                "screen_x": str(video["screen_x"]),
                "screen_y": str(video["screen_y"]),
                "file_size": str(video["file_size"]),
                "file_duration": str(video["file_duration"]),
                "orientation": video["orientation"],
            },
            files={"file": (video["filename"], handle, "video/mp4")},
        )
    body = require_success(result, "上传视频")
    context = {
        "local_file": video["path"],
        "filename": video["filename"],
        "screen_x": video["screen_x"],
        "screen_y": video["screen_y"],
        "file_size": video["file_size"],
        "file_duration": video["file_duration"],
        "orientation": video["orientation"],
        "upload_id": body.get("upload_id"),
        "media_url": body.get("media_url"),
        "media_cover_url": body.get("media_cover_url"),
        "file_path": body.get("file_path"),
    }
    return context


def ensure_upload_window(upload_id, timeout=300, poll_interval=DEFAULT_POLL_INTERVAL):
    """根据 upload_id 创建/轮询 window_id。"""
    deadline = time.time() + timeout
    last_body = None

    while True:
        result = api_request(
            ICENTER_API,
            "/manus/uploads",
            method="POST",
            json_data={"upload_ids": [int(upload_id)]},
        )
        body = require_success(result, "获取上传 window")
        last_body = body
        status = body.get("status")
        window_id = body.get("window_id") or 0

        if status not in RUNNING_STATUSES and window_id:
            return body
        if time.time() >= deadline:
            raise InbeidouError(
                f"等待 window_id 超时: upload_id={upload_id}, status={status}, last={json.dumps(last_body, ensure_ascii=False)}"
            )
        time.sleep(poll_interval)


def upload_video(file_path, timeout=300, poll_interval=DEFAULT_POLL_INTERVAL):
    """完整上传链路: 上传原视频 -> 轮询 window_id。"""
    context = upload_raw_media(file_path)
    window_body = ensure_upload_window(
        context["upload_id"],
        timeout=timeout,
        poll_interval=poll_interval,
    )
    context.update(
        {
            "window_id": window_body.get("window_id"),
            "window_status": window_body.get("status"),
            "agent_id": window_body.get("agent_id"),
            "manus_id": window_body.get("manus_id"),
            "manus_status": window_body.get("manus_status"),
        }
    )
    save_state(context)
    return context


def resolve_media_context(args):
    """优先从参数获取媒资上下文；缺省则回退到最近一次上传。"""
    if getattr(args, "file", None):
        return upload_video(
            args.file,
            timeout=getattr(args, "upload_timeout", 300),
            poll_interval=getattr(args, "poll_interval", DEFAULT_POLL_INTERVAL),
        )

    state = load_state()
    upload_id = getattr(args, "upload_id", None) or state.get("upload_id")
    if not upload_id:
        raise InbeidouError("缺少媒资参数，请传 --file 或 --upload-id")

    window_id = getattr(args, "window_id", None)
    if not window_id:
        if str(state.get("upload_id")) == str(upload_id):
            window_id = state.get("window_id")
        if not window_id:
            window_body = ensure_upload_window(
                upload_id,
                timeout=getattr(args, "upload_timeout", 300),
                poll_interval=getattr(args, "poll_interval", DEFAULT_POLL_INTERVAL),
            )
            window_id = window_body.get("window_id")

    context = {
        "upload_id": int(upload_id),
        "window_id": int(window_id),
        "local_file": state.get("local_file"),
        "filename": state.get("filename"),
        "media_url": state.get("media_url"),
        "media_cover_url": state.get("media_cover_url"),
    }
    save_state(context)
    return context


def normalize_publish_platform(value):
    if not value:
        return ""
    platform = str(value).strip().upper()
    if platform not in PUBLISH_SOCIAL_TYPES:
        raise InbeidouError(
            f"不支持的平台: {value}，可选值: {', '.join(PUBLISH_SOCIAL_TYPES)}"
        )
    return platform


def split_cli_values(values):
    items = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part:
                items.append(part)
    return items


def parse_schedule_at(value):
    if not value:
        return None
    raw = value.strip()
    formats = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]
    for pattern in formats:
        try:
            parsed = datetime.strptime(raw, pattern)
            break
        except ValueError:
            parsed = None
    if parsed is None:
        raise InbeidouError("定时发布时间格式错误，请使用 'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD HH:MM:SS'")

    min_time = datetime.now() + timedelta(minutes=5)
    max_time = datetime.now() + timedelta(days=31)
    if parsed < min_time:
        raise InbeidouError("定时发布时间至少需要晚于当前时间 5 分钟")
    if parsed > max_time:
        raise InbeidouError("定时发布时间不能超过 31 天")
    return parsed.strftime("%Y-%m-%d %H:%M:00")


def get_publish_text(args):
    if getattr(args, "text", None):
        return args.text.strip()
    if getattr(args, "text_file", None):
        path = Path(args.text_file).expanduser().resolve()
        if not path.exists():
            raise InbeidouError(f"文案文件不存在: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    raise InbeidouError("缺少帖子内容，请传 --text 或 --text-file")


def resolve_publish_file_url(args):
    if getattr(args, "file", None):
        return upload_publish_file(args.file)["publish_file_url"]
    if getattr(args, "file_url", None):
        return args.file_url.strip()

    state = load_state()
    file_url = state.get("publish_file_url")
    if not file_url:
        raise InbeidouError("缺少发布视频，请传 --file、--file-url，或先执行 publish upload")
    return file_url


def resolve_publish_targets(args):
    account_ids = split_cli_values(getattr(args, "account_id", None))
    team_ids = split_cli_values(getattr(args, "team_id", None))

    if account_ids:
        accounts = require_success(get_publish_accounts(), "获取发布账号列表")
        selected = [account for account in accounts if str(account.get("id")) in set(account_ids)]
        found_ids = {str(account.get("id")) for account in selected}
        missing = [account_id for account_id in account_ids if account_id not in found_ids]
        if missing:
            raise InbeidouError(f"未找到账号 ID: {', '.join(missing)}")

        invalid = [
            account
            for account in selected
            if account.get("status") != 0 or not account.get("team_id")
        ]
        if invalid:
            raise InbeidouError(
                "选择的账号包含不可发布项: "
                + ", ".join(str(account.get("id")) for account in invalid)
            )

        social_types = {account.get("type") for account in selected}
        if len(social_types) != 1:
            raise InbeidouError("一次发布只能选择同一平台的账号")

        return {
            "social_type": next(iter(social_types)),
            "team_ids": [account.get("team_id") for account in selected],
            "accounts": selected,
        }

    if team_ids:
        social_type = normalize_publish_platform(getattr(args, "platform", ""))
        return {
            "social_type": social_type,
            "team_ids": team_ids,
            "accounts": [],
        }

    raise InbeidouError("请传 --account-id 或 --team-id 指定发布目标")


def build_publish_payload(args):
    target = resolve_publish_targets(args)
    file_url = resolve_publish_file_url(args)
    text = get_publish_text(args)
    post_date = parse_schedule_at(getattr(args, "schedule_at", None))

    payload = {
        "team_id": ",".join(target["team_ids"]),
        "text": text,
        "file_url": file_url,
        "post_status": PUBLISH_POST_STATUS_VALUE["scheduled" if post_date else "published"],
        "social_type": target["social_type"],
    }
    if post_date:
        payload["post_date"] = post_date
    if target["social_type"] in {"FACEBOOK", "INSTAGRAM"}:
        payload["type"] = "REEL"

    return payload, target


def describe_publish_accounts(accounts):
    print(f"\n📣 已授权发布账号 (共 {len(accounts)} 个)")
    print("=" * 140)
    print(
        f"{'ID':<6} {'平台':<12} {'昵称':<28} {'状态':<18} {'team_id':<38} {'频道'}"
    )
    print("-" * 140)
    for account in accounts:
        channel_names = ",".join(channel.get("name", "") for channel in account.get("channels", [])[:2])
        status = PUBLISH_ACCOUNT_STATUSES.get(account.get("status"), str(account.get("status")))
        print(
            f"{str(account.get('id')):<6} "
            f"{PUBLISH_SOCIAL_NAMES.get(account.get('type'), account.get('type', '')):<12} "
            f"{str(account.get('social_name', ''))[:26]:<28} "
            f"{status:<18} "
            f"{str(account.get('team_id', '')):<38} "
            f"{channel_names}"
        )


def describe_publish_records(body):
    items = body.get("items", [])
    total = body.get("page", {}).get("total_count", 0)
    print(f"\n🗂️ 发布记录 (共 {total} 条)")
    print("=" * 150)
    print(
        f"{'ID':<6} {'平台':<12} {'账号':<24} {'状态':<12} {'发布时间':<20} {'team_id':<38} {'task_id'}"
    )
    print("-" * 150)
    for item in items:
        print(
            f"{str(item.get('id', '')):<6} "
            f"{PUBLISH_SOCIAL_NAMES.get(item.get('social_type'), item.get('social_type', '')):<12} "
            f"{str(item.get('social_name', ''))[:22]:<24} "
            f"{str(item.get('status', '')):<12} "
            f"{str(item.get('post_date', '')):<20} "
            f"{str(item.get('team_id', '')):<38} "
            f"{str(item.get('task_id', ''))}"
        )


def analyze_video(upload_id, window_id, timeout=600, poll_interval=DEFAULT_POLL_INTERVAL):
    """智影解析轮询。"""
    deadline = time.time() + timeout
    last_body = None

    while True:
        result = api_request(
            ICENTER_API,
            "/manus/vision/analyze_v3",
            method="POST",
            json_data={"window_id": int(window_id), "upload_ids": [int(upload_id)]},
        )
        body = require_success(result, "智影解析")
        last_body = body
        if body.get("status") not in RUNNING_STATUSES:
            save_state({"last_analysis": body})
            return body
        if time.time() >= deadline:
            raise InbeidouError(f"等待智影解析超时: {json.dumps(last_body, ensure_ascii=False)}")
        time.sleep(poll_interval)


def describe_analysis(body):
    """友好打印解析结果摘要。"""
    duration_map = body.get("file_duration", {})
    duration = next(iter(duration_map.values()), 0)
    print("\n🧠 智影解析")
    print("=" * 80)
    print(f"   window_id: {body.get('window_id')}")
    print(f"   upload_ids: {body.get('upload_ids')}")
    print(f"   时长: {format_seconds(duration)}")
    print(f"   状态: {body.get('status')}")

    sections = [
        ("golden_seconds", "🎯 黄金片段"),
        ("excitement", "✨ 亮点解析"),
        ("importance", "📚 剧情解析"),
        ("twist", "🪝 结尾悬念"),
    ]
    for key, title in sections:
        items = body.get(key, {}).get("items_v3", [])
        if not items:
            continue
        print(f"\n{title}")
        print("-" * 80)
        for item in items[:5]:
            content = item.get("content") or "(无文案)"
            print(f"   [{item.get('timestamp')}] score={item.get('score')}  {content}")

    emotional = body.get("emotional", {}).get("items_v3", [])
    if emotional:
        peak = max(emotional, key=lambda item: item.get("score", 0))
        print("\n📈 情绪峰值")
        print("-" * 80)
        print(
            f"   [{peak.get('timestamp')}] score={peak.get('score')} play_time={peak.get('play_time')}"
        )


def build_high_cut_params(args):
    """按前端真实逻辑构造高燃剪辑参数。"""
    deduplication = args.deduplication or DEFAULT_DEDUPLICATION
    params = {
        "watermark": args.watermark or "",
        "cut_duration": args.duration,
        "output_count": args.output_count,
        "cut_type": args.cut_type,
        "script_count": args.script_count,
    }
    for key in deduplication:
        params[key] = True
    return params


def alignment_to_subtitle_x(alignment):
    """前端字幕对齐 -> subtitle_x。"""
    if alignment == "Right":
        return 0.9999
    if alignment == "Left":
        return 0
    return 0.5


def build_translate_params(args):
    """按前端真实逻辑构造翻译参数。"""
    config = dict(DEFAULT_TRANSLATE_CONFIG)
    config.update(
        {
            "source_language": args.source_lang,
            "target_language": args.target_lang,
            "need_speech_translate": not args.no_speech_translate,
            "subtitle_type": args.subtitle_type,
            "subtitle_y": args.subtitle_y,
            "font": args.font,
            "font_size": args.font_size,
            "font_color": args.font_color,
            "alignment": args.alignment,
            "font_face_bold": args.bold,
            "font_face_underline": args.underline,
            "font_face_italic": args.italic,
            "font_color_opacity": args.font_opacity,
            "effect_color_style": args.effect_style or "",
            "shadow": args.shadow,
            "shadow_shift": args.shadow_shift,
            "shadow_x_bord": args.shadow_x_bord,
            "shadow_y_bord": args.shadow_y_bord,
            "shadow_opacity": args.shadow_opacity,
            "outline": args.outline,
            "outline_board": args.outline_board,
        }
    )

    subtitle_y = 0.99 if config["subtitle_y"] >= 100 else config["subtitle_y"] / 100
    params = {
        "source_language": config["source_language"],
        "target_language": config["target_language"],
        "need_speech_translate": config["need_speech_translate"],
        "subtitle_type": config["subtitle_type"],
        "subtitle_x": alignment_to_subtitle_x(config["alignment"]),
        "subtitle_y": subtitle_y,
        "font": config["font"],
        "font_size": config["font_size"],
        "font_color": config["font_color"],
        "alignment": config["alignment"],
        "font_face_bold": config["font_face_bold"],
        "font_face_underline": config["font_face_underline"],
        "font_face_italic": config["font_face_italic"],
        "font_color_opacity": str(config["font_color_opacity"] / 100),
        "effect_color_style": config["effect_color_style"],
        "ocr_area_x": -1,
        "ocr_area_y": -1,
        "ocr_area_width": -1,
        "ocr_area_height": -1,
    }

    if not config["effect_color_style"] and config["shadow"]:
        params.update(
            {
                "shadow_shift": config["shadow_shift"] / 30,
                "shadow_x_bord": config["shadow_x_bord"] / 30,
                "shadow_y_bord": config["shadow_y_bord"] / 30,
                "shadow_opacity": str(config["shadow_opacity"] / 100),
            }
        )
    else:
        params.update(
            {
                "shadow_shift": -1,
                "shadow_x_bord": -1,
                "shadow_y_bord": -1,
                "shadow_opacity": "",
            }
        )

    if not config["effect_color_style"] and config["outline"]:
        params["outline_board"] = config["outline_board"]
    else:
        params["outline_board"] = -1

    return params


def submit_ws_tasks(window_id, upload_ids, tasks, merge_video=False, timeout=90):
    """通过前端同款 websocket 提交智能任务。"""
    token = load_auth_token()
    payload = {
        "question": "",
        "upload_ids": [int(upload_id) for upload_id in upload_ids],
        "window_id": int(window_id),
        "msg_type": "card",
        "token": token,
        "merge_video": bool(merge_video),
        "tasks": tasks,
    }

    try:
        ws = create_connection(WS_MANUS_CHATS, timeout=timeout)
    except Exception as exc:
        raise InbeidouError(f"建立 WebSocket 连接失败: {exc}") from exc

    try:
        ws.send(json.dumps(payload, ensure_ascii=False))
        deadline = time.time() + timeout
        last_message = None

        while time.time() < deadline:
            try:
                message = ws.recv()
            except WebSocketTimeoutException:
                continue
            if message == "pong":
                continue
            try:
                data = json.loads(message)
            except ValueError:
                continue
            last_message = data
            if data.get("msg_type") == "error":
                raise InbeidouError(f"任务提交失败: {data.get('body') or data.get('msg_type')}")
            if data.get("is_end"):
                return data

        raise InbeidouError(f"等待任务受理超时: {json.dumps(last_message, ensure_ascii=False)}")
    except WebSocketException as exc:
        raise InbeidouError(f"WebSocket 任务提交异常: {exc}") from exc
    finally:
        try:
            ws.close()
        except Exception:
            pass


def wait_for_manus(manus_id, timeout=DEFAULT_TASK_TIMEOUT, poll_interval=DEFAULT_POLL_INTERVAL):
    """轮询 manus 直到完成。"""
    deadline = time.time() + timeout
    last_body = None

    while True:
        result = get_manus_detail(manus_id)
        body = require_success(result, "查询作品详情")
        last_body = body
        status = body.get("status")
        if status not in RUNNING_STATUSES:
            save_state({"last_manus_id": manus_id, "last_manus_status": status})
            return body
        if time.time() >= deadline:
            raise InbeidouError(f"等待作品生成超时: manus_id={manus_id}, status={status}")
        time.sleep(poll_interval)


def first_output_media_url(body):
    """取作品详情中的首个输出视频 URL。"""
    media = body.get("media") or []
    if media:
        return media[0].get("media_url")
    return body.get("video_url")


def describe_manus(body):
    """打印作品详情。"""
    print("\n🎬 作品详情")
    print("=" * 80)
    print(f"   manus_id: {body.get('id')}")
    print(f"   task_name: {body.get('task_name')}")
    print(f"   status: {body.get('status')}")
    print(f"   history_id: {body.get('history_id')}")
    print(f"   window_id: {body.get('window_id')}")
    print(f"   created_time: {body.get('created_time')}")
    media = body.get("media") or []
    if media:
        print(f"   输出数量: {len(media)}")
        for idx, item in enumerate(media, start=1):
            print(f"   输出{idx}: {item.get('media_url')}")
    elif body.get("video_url"):
        print(f"   视频URL: {body.get('video_url')}")
    if body.get("cover_url"):
        print(f"   封面: {body.get('cover_url')}")


def download_manus(manus_id, output_dir="."):
    """下载作品视频。"""
    result = get_manus_detail(manus_id)
    body = require_success(result, "获取作品详情")
    media_url = first_output_media_url(body)
    if not media_url:
        raise InbeidouError("作品暂无可下载视频")

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    title = body.get("task_name") or body.get("title") or f"manus_{manus_id}"
    safe_title = "".join(ch for ch in title if ch not in '\\/:*?"<>|').strip() or f"manus_{manus_id}"
    target = output_path / f"{safe_title}.mp4"
    if target.exists():
        target = output_path / f"{safe_title}_manus_{manus_id}.mp4"

    try:
        response = requests.get(media_url, timeout=120)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise InbeidouError(f"下载失败: {exc}") from exc

    target.write_bytes(response.content)
    return str(target)


def filter_languages_payload(data, view_type):
    if view_type == "speech":
        return {"speech_source_language": data.get("speech_source_language", [])}
    if view_type == "target":
        return {"speech_target_language": data.get("speech_target_language", [])}
    if view_type == "subtitle":
        return {
            "subtitle_source_language": data.get("subtitle_source_language", []),
            "subtitle_target_language": data.get("subtitle_target_language", []),
        }
    return data


def cmd_user(args):
    body = require_success(get_user_info(), "获取用户信息")
    if getattr(args, "json", False):
        pretty_print_json(body)
        return
    print("\n👤 用户信息")
    print("=" * 40)
    print(f"   用户ID: {body.get('agent_id')}")
    print(f"   昵称: {body.get('nickname')}")
    print(f"   手机: {body.get('phone')}")
    print(f"   邀请码: {body.get('invite_code')}")
    print(f"   分佣比例: {body.get('share_rate')}%")
    print(f"   总收入: ¥{body.get('total_income')}")


def cmd_credit(args):
    body = require_success(get_credit(), "获取积分余额")
    if getattr(args, "json", False):
        pretty_print_json(body)
        return
    print("\n💰 积分余额")
    print("=" * 40)
    print(f"   总积分: {body.get('total')}")
    print(f"   购买积分: {body.get('buy')}")
    print(f"   赠送积分: {body.get('gift')}")
    print(f"   VIP积分: {body.get('vip')}")


def cmd_products(args):
    products = require_success(get_products(), "获取产品列表")
    if getattr(args, "json", False):
        pretty_print_json(products)
        return
    print("\n🛠️ AI工具/产品")
    print("=" * 80)
    print(f"{'ID':<4} {'名称':<35} {'原价':<10} {'折扣价'}")
    print("-" * 80)
    for product in products:
        print(
            f"{product.get('id'):<4} {product.get('name'):<35} {product.get('credit'):<10} {product.get('discount_credit')}"
        )


def cmd_languages(args):
    data = require_success(get_translation_languages(), "获取翻译语言")
    if getattr(args, "json", False):
        pretty_print_json(filter_languages_payload(data, args.type))
        return

    if args.type in ("all", "speech"):
        print("\n🎤 语音支持语言")
        print("-" * 40)
        for lang in data.get("speech_source_language", []):
            print(f"   {lang.get('code'):<10} {lang.get('name')}")

    if args.type in ("all", "target"):
        print("\n🎯 目标语言")
        print("-" * 40)
        for lang in data.get("speech_target_language", []):
            print(f"   {lang.get('code'):<10} {lang.get('name')}")

    if args.type in ("all", "subtitle"):
        print("\n📝 字幕语言")
        print("-" * 40)
        print("   源语言:")
        for lang in data.get("subtitle_source_language", []):
            print(f"      {lang.get('code'):<10} {lang.get('name')}")
        print("   目标语言:")
        for lang in data.get("subtitle_target_language", []):
            print(f"      {lang.get('code'):<10} {lang.get('name')}")


def cmd_publish(args):
    if args.action == "accounts":
        accounts = require_success(get_publish_accounts(), "获取发布账号列表")
        if args.platform:
            platform = normalize_publish_platform(args.platform)
            accounts = [account for account in accounts if account.get("type") == platform]
        if args.status is not None:
            accounts = [account for account in accounts if int(account.get("status", -1)) == args.status]
        if args.json:
            pretty_print_json(accounts)
            return
        describe_publish_accounts(accounts)
        return

    if args.action == "upload":
        context = upload_publish_file(args.file)
        if args.json:
            pretty_print_json(context)
            return
        print("\n📤 发布视频上传成功")
        print("=" * 80)
        print(f"   文件: {context.get('publish_local_file')}")
        print(f"   file_url: {context.get('publish_file_url')}")
        print(f"   size: {format_size(context.get('publish_upload_size'))}")
        print(f"   mime: {context.get('publish_upload_mime')}")
        return

    if args.action == "create":
        payload, target = build_publish_payload(args)
        if args.dry_run:
            pretty_print_json({"payload": payload, "target": target})
            return

        body = require_success(create_publish_post(payload), "发布帖子")
        tasks = body.get("tasks", [])
        save_state({"last_publish_payload": payload, "last_publish_tasks": tasks})

        if args.json:
            pretty_print_json({"payload": payload, "tasks": tasks})
            return

        print("\n🚀 发布任务已提交")
        print("=" * 80)
        print(f"   平台: {PUBLISH_SOCIAL_NAMES.get(payload.get('social_type'), payload.get('social_type'))}")
        print(f"   team_id: {payload.get('team_id')}")
        print(f"   post_status: {'scheduled' if payload.get('post_status') == 1 else 'published'}")
        if payload.get("post_date"):
            print(f"   post_date: {payload.get('post_date')}")
        for index, task in enumerate(tasks, start=1):
            print(
                f"   任务{index}: team_id={task.get('team_id')} task_id={task.get('task_id')} "
                f"status={task.get('status')} message={task.get('message')}"
            )
        return

    if args.action == "records":
        post_status = PUBLISH_POST_STATUS_VALUE[args.post_status]
        body = require_success(
            get_publish_records(
                page=args.page,
                page_size=args.size,
                post_status=post_status,
                status=args.status,
                social_type=args.platform,
                social_id=args.social_id,
            ),
            "获取发布记录",
        )
        if args.json:
            pretty_print_json(body)
            return
        describe_publish_records(body)
        return

    if args.action == "delete":
        require_success(
            delete_publish_post(
                post_id=args.post_id or "",
                team_id=args.team_id,
                task_id=args.task_id,
            ),
            "删除发布记录",
        )
        if getattr(args, "json", False):
            pretty_print_json(
                {
                    "success": True,
                    "team_id": args.team_id,
                    "task_id": args.task_id,
                    "post_id": args.post_id or "",
                }
            )
            return
        print("删除成功!")


def cmd_list(args):
    result = get_tasks(
        page=args.page,
        page_size=args.size,
        platform=args.platform,
        language=args.language,
        search=args.search,
        order=args.order,
    )
    body = require_success(result, "获取短剧列表")
    items = body.get("data", [])
    page_info = body.get("page", {})
    total = page_info.get("total_count", 0)

    if getattr(args, "json", False):
        pretty_print_json(body)
        return

    print(f"\n共找到 {total} 个短剧")
    if not items:
        print("暂无数据")
        return

    for item in items:
        format_drama(item)

    current = page_info.get("current_page", args.page)
    total_pages = max(1, math.ceil(total / args.size)) if args.size else 1
    print(f"\n{'=' * 60}")
    print(f"第 {current} / {total_pages} 页")


def cmd_detail(args):
    item = resolve_task_for_detail(args)
    cover_url = pick_cover_url(item)
    episode_info = {}
    online_video_url = pick_video_url(item)

    if not args.no_episode_info and item.get("serial_id"):
        episode_body = require_success(
            get_episode_info(
                serial_id=item["serial_id"],
                episode_order=args.episode_order,
                app_id=item.get("app_id") or args.platform,
                task_type=item.get("task_type", args.task_type),
                need_play=1,
            ),
            "获取短剧分集在线视频",
        )
        episode_info = episode_body or {}
        online_video_url = pick_video_url(episode_info) or online_video_url

    promotion_links = []
    if not args.no_promotion_links:
        for platform_id in normalize_promotion_platforms(args.promote_platforms, include_all=args.all_promote_platforms):
            payload = require_success(
                receive_task(task_id=item["task_id"], task_type=item.get("task_type", args.task_type), platform=platform_id),
                f"获取 {PROMOTION_PLATFORMS[platform_id]} 推广链接",
            )
            promotion_links.append(build_promotion_link_entry(platform_id, payload))

    result = {
        **item,
        "cover_url": cover_url,
        "episode_order": args.episode_order,
        "episode_info": episode_info,
        "online_video_url": online_video_url,
        "promotion_links": promotion_links,
    }

    if getattr(args, "json", False):
        pretty_print_json(result)
        return

    format_drama(item)
    print(f"   serial_id: {item.get('serial_id', 'N/A')}")
    print(f"   third_serial_id: {item.get('third_serial_id', 'N/A')}")
    print(f"   cover_url: {cover_url or 'N/A'}")
    print(f"   第{args.episode_order}集在线视频: {online_video_url or 'N/A'}")
    print("   简介:")
    print(f"   {item.get('description', '') or 'N/A'}")

    if args.no_promotion_links:
        return

    print(f"\n{'=' * 60}")
    print("推广链接")
    print(f"{'=' * 60}")
    if not promotion_links:
        print("暂无推广链接")
        return

    for entry in promotion_links:
        print(f"\n[{entry['platform_name']}]")
        print(f"  app_link: {entry.get('app_link') or 'N/A'}")
        print(f"  serial_link: {entry.get('serial_link') or 'N/A'}")
        print(f"  code: {entry.get('code') or 'N/A'}")
        if entry.get("tiktok_dramago_link"):
            print(f"  tiktok_dramago_link: {entry['tiktok_dramago_link']}")
        if entry.get("tiktok_url"):
            print(f"  tiktok_url: {entry['tiktok_url']}")
        if entry.get("promote_code_content"):
            print("  promote_code_content:")
            print(f"  {entry['promote_code_content']}")


def cmd_uploads(args):
    if args.action == "list":
        body = require_success(get_uploads(page=args.page, page_size=args.size), "获取媒资库列表")
        items = body.get("items", [])
        total = body.get("page", {}).get("total_count", 0)

        if getattr(args, "json", False):
            pretty_print_json(body)
            return

        print(f"\n📁 媒资库视频 (共 {total} 个)")
        print("=" * 110)
        print(f"{'ID':<8} {'文件名':<36} {'方向':<10} {'时长':<10} {'大小':<12} {'状态':<12} {'上传时间'}")
        print("-" * 110)
        for item in items:
            size_value = item.get("file_size") or item.get("size")
            print(
                f"{item.get('id', ''):<8} "
                f"{str(item.get('filename', '未知'))[:34]:<36} "
                f"{str(item.get('orientation', '')):<10} "
                f"{format_seconds(item.get('file_duration', 0)):<10} "
                f"{(format_size(size_value) if size_value else '-'): <12} "
                f"{str(item.get('status', '')):<12} "
                f"{str(item.get('created_at', ''))[:19]}"
            )

        total_pages = max(1, math.ceil(total / args.size)) if args.size else 1
        print(f"\n第 {args.page} / {total_pages} 页")
        return

    if args.action == "upload":
        context = upload_video(args.file, timeout=args.upload_timeout, poll_interval=args.poll_interval)
        if args.json:
            pretty_print_json(context)
            return

        print("\n✅ 上传成功")
        print("=" * 80)
        print(f"   文件: {context.get('local_file')}")
        print(f"   upload_id: {context.get('upload_id')}")
        print(f"   window_id: {context.get('window_id')}")
        print(f"   分辨率: {context.get('screen_x')}x{context.get('screen_y')}")
        print(f"   方向: {context.get('orientation')}")
        print(f"   时长: {format_seconds(context.get('file_duration'))}")
        print(f"   大小: {format_size(context.get('file_size'))}")
        print(f"   media_url: {context.get('media_url')}")
        return

    if args.action == "delete":
        require_success(delete_upload(args.file_id), "删除媒资")
        if getattr(args, "json", False):
            pretty_print_json({"success": True, "file_id": args.file_id})
            return
        print("删除成功!")


def cmd_analyze(args):
    context = resolve_media_context(args)
    body = analyze_video(
        upload_id=context["upload_id"],
        window_id=context["window_id"],
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )
    if args.json:
        pretty_print_json(body)
        return
    describe_analysis(body)


def cmd_clip(args):
    if args.action == "types":
        body = require_success(get_clip_types(), "获取剪辑类型")
        if args.json:
            pretty_print_json(body)
            return
        print("\n✂️ 剪辑枚举")
        print("=" * 60)
        for key, value in body.items():
            print(f"{key}: {value}")
        return

    context = resolve_media_context(args)
    task = {"key": HIGH_CUT_TASK_KEY, "params": build_high_cut_params(args)}
    submit = submit_ws_tasks(
        window_id=context["window_id"],
        upload_ids=[context["upload_id"]],
        tasks=[task],
        merge_video=args.merge_video,
        timeout=args.submit_timeout,
    )

    manus_id = submit.get("manus_id")
    save_state({"last_manus_id": manus_id, "last_clip_submit": submit})

    if args.json and not args.wait:
        pretty_print_json({"submit": submit, "context": context, "task": task})
        return

    print("\n🚀 智能剪辑任务已提交")
    print("=" * 80)
    print(f"   upload_id: {context.get('upload_id')}")
    print(f"   window_id: {context.get('window_id')}")
    print(f"   manus_id: {manus_id}")
    print(f"   history_id: {submit.get('history_id')}")
    print(f"   group_id: {submit.get('group_id')}")

    if not args.wait:
        return

    body = wait_for_manus(manus_id, timeout=args.timeout, poll_interval=args.poll_interval)
    if args.json:
        pretty_print_json(body)
        return
    describe_manus(body)


def cmd_translate(args):
    if args.action == "languages":
        body = require_success(get_translation_languages(), "获取翻译语言")
        if args.json:
            pretty_print_json(body)
            return
        print("\n🌐 支持的翻译语言")
        print("=" * 60)
        for lang in body.get("speech_target_language", []):
            print(f"   {lang.get('code'):<12} {lang.get('name')}")
        return

    if args.action == "fonts":
        body = require_success(get_translation_fonts(), "获取翻译字体")
        if args.json:
            pretty_print_json(body)
            return
        print("\n🔤 支持的翻译字体")
        print("=" * 60)
        for font in body.get("fonts", []):
            print(f"   {font.get('code'):<24} {font.get('name')}")
        return

    if args.action == "styles":
        body = require_success(get_translation_effect_styles(), "获取字幕效果样式")
        if args.json:
            pretty_print_json(body)
            return
        print("\n🎨 字幕效果样式")
        print("=" * 60)
        for style in body.get("effect_color_styles", []):
            print(f"   {style.get('code')}")
        return

    context = resolve_media_context(args)
    task = {"key": TRANSLATE_TASK_KEY, "params": build_translate_params(args)}
    submit = submit_ws_tasks(
        window_id=context["window_id"],
        upload_ids=[context["upload_id"]],
        tasks=[task],
        merge_video=args.merge_video,
        timeout=args.submit_timeout,
    )
    manus_id = submit.get("manus_id")
    save_state({"last_manus_id": manus_id, "last_translate_submit": submit})

    if args.json and not args.wait:
        pretty_print_json({"submit": submit, "context": context, "task": task})
        return

    print("\n🌍 视频翻译任务已提交")
    print("=" * 80)
    print(f"   upload_id: {context.get('upload_id')}")
    print(f"   window_id: {context.get('window_id')}")
    print(f"   manus_id: {manus_id}")
    print(f"   source_language: {args.source_lang}")
    print(f"   target_language: {args.target_lang}")
    print(f"   history_id: {submit.get('history_id')}")
    print(f"   group_id: {submit.get('group_id')}")

    if not args.wait:
        return

    body = wait_for_manus(manus_id, timeout=args.timeout, poll_interval=args.poll_interval)
    if args.json:
        pretty_print_json(body)
        return
    describe_manus(body)


def cmd_manus(args):
    if args.action == "list":
        body = require_success(
            get_manus(page=args.page, page_size=args.size, task_name=args.search),
            "获取作品列表",
        )
        items = body.get("items", [])
        total = body.get("total", 0)

        if getattr(args, "json", False):
            pretty_print_json(body)
            return

        print(f"\n🎬 我的作品 (共 {total} 个)")
        print("=" * 90)
        print(f"{'ID':<10} {'标题':<40} {'状态':<12} {'创建时间'}")
        print("-" * 90)
        for item in items:
            manus_id = item.get("manus_id", "")
            task_name = item.get("task_name", "")
            title = (item.get("title") or task_name or "")[:38]
            status = item.get("status", "未知")
            created_at = str(item.get("created_time", ""))[:19]
            print(f"{manus_id:<10} {title:<40} {status:<12} {created_at}")

        total_pages = max(1, math.ceil(total / args.size)) if args.size else 1
        print(f"\n第 {args.page} / {total_pages} 页")
        return

    if args.action == "detail":
        body = require_success(get_manus_detail(args.manus_id), "获取作品详情")
        if args.json:
            pretty_print_json(body)
            return
        describe_manus(body)
        return

    if args.action == "download":
        path = download_manus(args.manus_id, args.output or ".")
        if getattr(args, "json", False):
            pretty_print_json({"success": True, "manus_id": args.manus_id, "path": path})
            return
        print(f"下载成功: {path}")
        return

    if args.action == "delete":
        require_success(delete_manus(args.manus_id), "删除作品")
        if getattr(args, "json", False):
            pretty_print_json({"success": True, "manus_id": args.manus_id})
            return
        print("删除成功!")


def build_parser():
    parser = argparse.ArgumentParser(
        description="北斗智影 AI 创作者中心 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 inbeidou_cli.py publish accounts
  python3 inbeidou_cli.py publish upload --file /path/to/video.mp4
  python3 inbeidou_cli.py publish create --account-id 109 --text "文案" --file /path/to/video.mp4
  python3 inbeidou_cli.py uploads upload --file /path/to/video.mp4
  python3 inbeidou_cli.py analyze run --file /path/to/video.mp4
  python3 inbeidou_cli.py clip create --file /path/to/video.mp4 --wait
  python3 inbeidou_cli.py translate create --upload-id 69458 --lang en --wait
  python3 inbeidou_cli.py manus detail --id 12345
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    user_parser = subparsers.add_parser("user", help="查看用户信息")
    user_parser.add_argument("--json", action="store_true", help="输出 JSON")

    credit_parser = subparsers.add_parser("credit", help="查看积分余额")
    credit_parser.add_argument("--json", action="store_true", help="输出 JSON")

    products_parser = subparsers.add_parser("products", help="查看 AI 工具/产品列表及价格")
    products_parser.add_argument("--json", action="store_true", help="输出 JSON")

    lang_parser = subparsers.add_parser("languages", help="查看支持的翻译语言")
    lang_parser.add_argument("--type", choices=["all", "speech", "target", "subtitle"], default="all")
    lang_parser.add_argument("--json", action="store_true", help="输出 JSON")

    publish_parser = subparsers.add_parser("publish", help="矩阵发布")
    publish_subparsers = publish_parser.add_subparsers(dest="action", help="操作", required=True)

    publish_accounts = publish_subparsers.add_parser("accounts", help="列出已授权发布账号")
    publish_accounts.add_argument("--platform", help=f"按平台筛选，可选: {', '.join(PUBLISH_SOCIAL_TYPES)}")
    publish_accounts.add_argument("--status", type=int, choices=[0, 1, 2], help="按账号状态筛选")
    publish_accounts.add_argument("--json", action="store_true", help="输出 JSON")

    publish_upload = publish_subparsers.add_parser("upload", help="上传发布视频")
    publish_upload.add_argument("--file", required=True, help="本地视频文件路径")
    publish_upload.add_argument("--json", action="store_true", help="输出 JSON")

    publish_create = publish_subparsers.add_parser("create", help="创建发布任务")
    publish_create.add_argument("--account-id", action="append", help="发布账号 ID，可重复或逗号分隔")
    publish_create.add_argument("--team-id", action="append", help="team_id，可重复或逗号分隔")
    publish_create.add_argument("--platform", help=f"使用 --team-id 时指定平台，可选: {', '.join(PUBLISH_SOCIAL_TYPES)}")
    publish_create.add_argument("--text", help="帖子内容")
    publish_create.add_argument("--text-file", help="从文件读取帖子内容")
    publish_create.add_argument("--file", help="本地视频文件路径；传入后会先上传")
    publish_create.add_argument("--file-url", help="已上传视频 URL")
    publish_create.add_argument("--schedule-at", help="定时发布时间，格式 YYYY-MM-DD HH:MM[:SS]")
    publish_create.add_argument("--dry-run", action="store_true", help="只输出请求 payload，不真正提交")
    publish_create.add_argument("--json", action="store_true", help="输出 JSON")

    publish_records = publish_subparsers.add_parser("records", help="查看发布记录")
    publish_records.add_argument("--post-status", choices=["published", "scheduled"], default="published")
    publish_records.add_argument("--platform", help=f"按平台筛选，可选: {', '.join(PUBLISH_SOCIAL_TYPES)}")
    publish_records.add_argument("--social-id", help="按 social_id 筛选")
    publish_records.add_argument("--status", default="", help="按任务状态筛选，如 WAITING/POSTED/ERROR")
    publish_records.add_argument("--page", type=int, default=1, help="页码")
    publish_records.add_argument("--size", type=int, default=10, help="每页数量")
    publish_records.add_argument("--json", action="store_true", help="输出 JSON")

    publish_delete = publish_subparsers.add_parser("delete", help="删除发布记录/定时任务")
    publish_delete.add_argument("--team-id", required=True, help="team_id")
    publish_delete.add_argument("--task-id", required=True, help="task_id")
    publish_delete.add_argument("--post-id", default="", help="post_id，定时任务一般可留空")
    publish_delete.add_argument("--json", action="store_true", help="输出 JSON")

    uploads_parser = subparsers.add_parser("uploads", help="媒资库管理")
    uploads_subparsers = uploads_parser.add_subparsers(dest="action", help="操作", required=True)

    uploads_list = uploads_subparsers.add_parser("list", help="列出视频")
    uploads_list.add_argument("--page", type=int, default=1, help="页码")
    uploads_list.add_argument("--size", type=int, default=10, help="每页数量")
    uploads_list.add_argument("--json", action="store_true", help="输出 JSON")

    uploads_upload = uploads_subparsers.add_parser("upload", help="上传视频")
    uploads_upload.add_argument("--file", type=str, required=True, help="视频文件路径")
    uploads_upload.add_argument("--upload-timeout", type=int, default=300, help="等待 window_id 超时秒数")
    uploads_upload.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="轮询间隔秒数")
    uploads_upload.add_argument("--json", action="store_true", help="输出 JSON")

    uploads_delete = uploads_subparsers.add_parser("delete", help="删除视频")
    uploads_delete.add_argument("--id", type=str, dest="file_id", required=True, help="文件 ID")
    uploads_delete.add_argument("--json", action="store_true", help="输出 JSON")

    analyze_parser = subparsers.add_parser("analyze", help="智影解析")
    analyze_subparsers = analyze_parser.add_subparsers(dest="action", help="操作", required=True)
    analyze_run = analyze_subparsers.add_parser("run", help="执行智影解析")
    analyze_run.add_argument("--file", type=str, help="本地视频路径；传入后会先上传")
    analyze_run.add_argument("--upload-id", type=int, help="已上传媒资 ID")
    analyze_run.add_argument("--window-id", type=int, help="对应 window_id，可省略自动补全")
    analyze_run.add_argument("--upload-timeout", type=int, default=300, help="等待 window_id 超时秒数")
    analyze_run.add_argument("--timeout", type=int, default=600, help="等待解析结果超时秒数")
    analyze_run.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="轮询间隔秒数")
    analyze_run.add_argument("--json", action="store_true", help="输出 JSON")

    clip_parser = subparsers.add_parser("clip", help="智能剪辑")
    clip_subparsers = clip_parser.add_subparsers(dest="action", help="操作", required=True)

    clip_types = clip_subparsers.add_parser("types", help="查看剪辑枚举")
    clip_types.add_argument("--json", action="store_true", help="输出 JSON")

    clip_create = clip_subparsers.add_parser("create", help="提交智能剪辑任务")
    clip_create.add_argument("--file", type=str, help="本地视频路径；传入后会先上传")
    clip_create.add_argument("--upload-id", type=int, help="已上传媒资 ID")
    clip_create.add_argument("--window-id", type=int, help="对应 window_id，可省略自动补全")
    clip_create.add_argument("--cut-type", choices=HIGH_CUT_CHOICES, default=DEFAULT_HIGH_CUT_CONFIG["cut_type"])
    clip_create.add_argument("--duration", default=DEFAULT_HIGH_CUT_CONFIG["cut_duration"], help="输出时长，默认 auto")
    clip_create.add_argument("--output-count", type=int, default=DEFAULT_HIGH_CUT_CONFIG["output_count"])
    clip_create.add_argument("--script-count", type=int, default=DEFAULT_HIGH_CUT_CONFIG["script_count"])
    clip_create.add_argument(
        "--deduplication",
        nargs="*",
        choices=DEDUPLICATION_CHOICES,
        default=None,
        help="去重策略列表",
    )
    clip_create.add_argument("--watermark", default=DEFAULT_HIGH_CUT_CONFIG["watermark"], help="水印文案")
    clip_create.add_argument("--merge-video", action="store_true", help="合并多段视频")
    clip_create.add_argument("--wait", action="store_true", help="等待任务完成")
    clip_create.add_argument("--upload-timeout", type=int, default=300, help="等待 window_id 超时秒数")
    clip_create.add_argument("--submit-timeout", type=int, default=90, help="等待 websocket 受理超时秒数")
    clip_create.add_argument("--timeout", type=int, default=DEFAULT_TASK_TIMEOUT, help="等待成片超时秒数")
    clip_create.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="轮询间隔秒数")
    clip_create.add_argument("--json", action="store_true", help="输出 JSON")

    translate_parser = subparsers.add_parser("translate", help="视频翻译")
    translate_subparsers = translate_parser.add_subparsers(dest="action", help="操作", required=True)

    translate_langs = translate_subparsers.add_parser("languages", help="查看支持的语言")
    translate_langs.add_argument("--json", action="store_true", help="输出 JSON")

    translate_fonts = translate_subparsers.add_parser("fonts", help="查看支持的字体")
    translate_fonts.add_argument("--json", action="store_true", help="输出 JSON")

    translate_styles = translate_subparsers.add_parser("styles", help="查看字幕效果样式")
    translate_styles.add_argument("--json", action="store_true", help="输出 JSON")

    translate_create = translate_subparsers.add_parser("create", help="提交视频翻译任务")
    translate_create.add_argument("--file", type=str, help="本地视频路径；传入后会先上传")
    translate_create.add_argument("--upload-id", type=int, help="已上传媒资 ID")
    translate_create.add_argument("--window-id", type=int, help="对应 window_id，可省略自动补全")
    translate_create.add_argument("--source-lang", default=DEFAULT_TRANSLATE_CONFIG["source_language"], help="源语言代码")
    translate_create.add_argument("--lang", dest="target_lang", default=DEFAULT_TRANSLATE_CONFIG["target_language"], help="目标语言代码")
    translate_create.add_argument("--subtitle-type", choices=["double", "single"], default=DEFAULT_TRANSLATE_CONFIG["subtitle_type"])
    translate_create.add_argument("--no-speech-translate", action="store_true", help="关闭 AI 配音翻译")
    translate_create.add_argument("--font", default=DEFAULT_TRANSLATE_CONFIG["font"], help="字体 code")
    translate_create.add_argument("--font-size", type=int, default=DEFAULT_TRANSLATE_CONFIG["font_size"], help="字幕字号")
    translate_create.add_argument("--font-color", default=DEFAULT_TRANSLATE_CONFIG["font_color"], help="字幕颜色")
    translate_create.add_argument("--font-opacity", type=int, default=DEFAULT_TRANSLATE_CONFIG["font_color_opacity"], help="字幕透明度 0-100")
    translate_create.add_argument("--subtitle-y", type=float, default=DEFAULT_TRANSLATE_CONFIG["subtitle_y"], help="字幕纵向位置百分比 0-100")
    translate_create.add_argument("--alignment", choices=["Left", "Center", "Right"], default=DEFAULT_TRANSLATE_CONFIG["alignment"])
    translate_create.add_argument("--effect-style", default=DEFAULT_TRANSLATE_CONFIG["effect_color_style"], help="字幕效果样式 code")
    translate_create.add_argument("--bold", action="store_true", help="粗体")
    translate_create.add_argument("--underline", action="store_true", help="下划线")
    translate_create.add_argument("--italic", action="store_true", help="斜体")
    translate_create.add_argument("--shadow", action="store_true", help="启用阴影")
    translate_create.add_argument("--shadow-shift", type=float, default=DEFAULT_TRANSLATE_CONFIG["shadow_shift"])
    translate_create.add_argument("--shadow-x-bord", type=float, default=DEFAULT_TRANSLATE_CONFIG["shadow_x_bord"])
    translate_create.add_argument("--shadow-y-bord", type=float, default=DEFAULT_TRANSLATE_CONFIG["shadow_y_bord"])
    translate_create.add_argument("--shadow-opacity", type=int, default=DEFAULT_TRANSLATE_CONFIG["shadow_opacity"])
    translate_create.add_argument("--outline", action="store_true", help="启用描边")
    translate_create.add_argument("--outline-board", type=float, default=DEFAULT_TRANSLATE_CONFIG["outline_board"])
    translate_create.add_argument("--merge-video", action="store_true", help="合并多段视频")
    translate_create.add_argument("--wait", action="store_true", help="等待任务完成")
    translate_create.add_argument("--upload-timeout", type=int, default=300, help="等待 window_id 超时秒数")
    translate_create.add_argument("--submit-timeout", type=int, default=90, help="等待 websocket 受理超时秒数")
    translate_create.add_argument("--timeout", type=int, default=DEFAULT_TASK_TIMEOUT, help="等待成片超时秒数")
    translate_create.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="轮询间隔秒数")
    translate_create.add_argument("--json", action="store_true", help="输出 JSON")

    manus_parser = subparsers.add_parser("manus", help="我的作品")
    manus_subparsers = manus_parser.add_subparsers(dest="action", help="操作", required=True)

    manus_list = manus_subparsers.add_parser("list", help="列出作品")
    manus_list.add_argument("--page", type=int, default=1, help="页码")
    manus_list.add_argument("--size", type=int, default=40, help="每页数量")
    manus_list.add_argument("--search", type=str, default="", help="搜索关键词")
    manus_list.add_argument("--json", action="store_true", help="输出 JSON")

    manus_detail = manus_subparsers.add_parser("detail", help="查看作品详情")
    manus_detail.add_argument("--id", type=str, dest="manus_id", required=True, help="作品 ID")
    manus_detail.add_argument("--json", action="store_true", help="输出 JSON")

    manus_download = manus_subparsers.add_parser("download", help="下载作品")
    manus_download.add_argument("--id", type=str, dest="manus_id", required=True, help="作品 ID")
    manus_download.add_argument("--output", type=str, default=".", help="下载目录")
    manus_download.add_argument("--json", action="store_true", help="输出 JSON")

    manus_delete = manus_subparsers.add_parser("delete", help="删除作品")
    manus_delete.add_argument("--id", type=str, dest="manus_id", required=True, help="作品 ID")
    manus_delete.add_argument("--json", action="store_true", help="输出 JSON")

    list_parser = subparsers.add_parser("list", help="查看短剧列表")
    list_parser.add_argument("-p", "--platform", type=str, default="", help="平台(dramabox, shortmax等)")
    list_parser.add_argument("-l", "--language", type=str, default="2", help="语言 ID")
    list_parser.add_argument("-s", "--search", type=str, default="", help="搜索标题")
    list_parser.add_argument("--page", type=int, default=1, help="页码")
    list_parser.add_argument("--size", type=int, default=15, help="每页数量")
    list_parser.add_argument("--order", type=str, default="publish_at", help="排序字段")
    list_parser.add_argument("--json", action="store_true", help="输出 JSON")

    detail_parser = subparsers.add_parser("detail", help="查看短剧详情并获取推广链接")
    detail_parser.add_argument("--task-id", type=str, default="", help="任务 ID")
    detail_parser.add_argument("-p", "--platform", type=str, default="", help="平台 app_id，如 reelshort")
    detail_parser.add_argument("-l", "--language", type=str, default="2", help="语言 ID")
    detail_parser.add_argument("-s", "--search", type=str, default="", help="按标题搜索并取首个匹配")
    detail_parser.add_argument("--size", type=int, default=10, help="搜索候选数量")
    detail_parser.add_argument("--order", type=str, default="publish_at", help="搜索排序字段")
    detail_parser.add_argument("--task-type", type=str, default="1", help="任务类型")
    detail_parser.add_argument("--episode-order", type=int, default=1, help="拉取第几集的在线视频 URL")
    detail_parser.add_argument("--no-episode-info", action="store_true", help="不拉取分集在线视频信息")
    detail_parser.add_argument(
        "--promote-platform",
        dest="promote_platforms",
        action="append",
        default=[],
        help="推广平台，支持 1/2/3/4 或 TikTok/Facebook/Instagram/YouTube，可重复传入",
    )
    detail_parser.add_argument("--all-promote-platforms", action="store_true", help="拉取全部平台推广链接")
    detail_parser.add_argument("--no-promotion-links", action="store_true", help="只看详情，不拉取推广链接")
    detail_parser.add_argument("--json", action="store_true", help="输出 JSON")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == "user":
            cmd_user(args)
        elif args.command == "credit":
            cmd_credit(args)
        elif args.command == "products":
            cmd_products(args)
        elif args.command == "languages":
            cmd_languages(args)
        elif args.command == "publish":
            cmd_publish(args)
        elif args.command == "uploads":
            cmd_uploads(args)
        elif args.command == "analyze":
            cmd_analyze(args)
        elif args.command == "clip":
            cmd_clip(args)
        elif args.command == "translate":
            cmd_translate(args)
        elif args.command == "manus":
            cmd_manus(args)
        elif args.command == "list":
            cmd_list(args)
        elif args.command == "detail":
            cmd_detail(args)
        else:
            parser.print_help()
    except InbeidouError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
