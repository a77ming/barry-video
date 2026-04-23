---
name: barry-drama
description: Use Barry Drama when the user wants to find the newest or hottest short dramas on Dramabox, ShortMax, or other supported drama platforms, or wants you to pick one for them.
---

# Barry Drama

Primary tool: `barry_video_dramas`

Detail tool: `barry_video_drama_detail`

Defaults:

- For "最新" or "最近", sort by `publish_at`
- If no platform is given, use `dramabox`
- If the user asks you to choose one, fetch several results first, then pick one and explain briefly
- For a task detail page or a request for 查剧/找剧详情, use `barry_video_drama_detail`; it returns the normalized `cover_url`, first episode `online_video_url`, raw `episode_info`, and promotion links.
- Pass `episodeOrder` when the user asks for a specific episode video URL.
