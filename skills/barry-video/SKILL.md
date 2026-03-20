---
name: barry-video
description: Use Barry Video when the user wants to use Barry's full Inbeidou workflow in natural language, including account info, credit balance, AI product prices, short drama discovery, media upload, smart analysis, smart clipping, video translation, manus download, and social publishing to Facebook, Instagram, TikTok, or YouTube.
metadata: {"openclaw":{"skillKey":"barry-video","requires":{"anyBins":["python3","python"]},"os":["darwin","linux"]}}
homepage: https://creator.inbeidou.cn/tool
user-invocable: true
---

# Barry Video

Barry Video is the umbrella skill for Barry's Inbeidou package.

Use it when the user speaks naturally, for example:

- "我的积分是多少"
- "列出我能发 Facebook 的账号"
- "最新 dramabox 的新剧选一个"
- "把这个视频上传后做智能剪辑"
- "把这个视频翻译成英语"
- "把剪好的视频直接发到 Facebook"

## Prefer dedicated tools

- Account and pricing: `barry_video_user`, `barry_video_credit`, `barry_video_products`, `barry_video_languages`
- Drama discovery: `barry_video_dramas`
- Media and AI: `barry_video_uploads_list`, `barry_video_upload`, `barry_video_uploads_delete`, `barry_video_analyze`, `barry_video_clip_types`, `barry_video_clip`, `barry_video_translate_languages`, `barry_video_translate_fonts`, `barry_video_translate_styles`, `barry_video_translate`
- Generated works: `barry_video_manus_list`, `barry_video_manus_detail`, `barry_video_download_manus`, `barry_video_manus_delete`
- Publish: `barry_video_publish_accounts`, `barry_video_publish`, `barry_video_publish_records`, `barry_video_publish_delete`, `barry_video_pipeline`

## Routing

1. If the user asks a factual account question such as balance, products, or profile, call the matching account tool directly.
2. If the user asks for a latest drama, call `barry_video_dramas` with platform `dramabox` unless another platform is specified.
3. If the user asks to analyze, clip, or translate a local file, pass `file` directly to the AI media tool instead of forcing a separate upload step.
4. If the user asks to publish and there is no known account or team ID, call `barry_video_publish_accounts` first.
5. If the user asks for clip then publish in one sentence, prefer `barry_video_pipeline`.
6. After publishing, use `barry_video_publish_records` to confirm the final status.

## Read next

- Common natural-language patterns: [references/intents.md](references/intents.md)
