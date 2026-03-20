# Barry Video Intents

## Account

- "我的积分是多少" -> `barry_video_credit`
- "我是谁" / "当前账号信息" -> `barry_video_user`
- "北斗有哪些产品和价格" -> `barry_video_products`

## Drama

- "最新 dramabox 的新剧选一个" -> `barry_video_dramas` with `platform=dramabox`, `order=publish_at`, `size=10`
- "查 shortmax 最近的剧" -> `barry_video_dramas` with `platform=shortmax`

## Media

- "上传这个视频" -> `barry_video_upload`
- "分析这个视频" -> `barry_video_analyze`
- "剪成高燃短视频" -> `barry_video_clip`
- "翻译成英语" -> `barry_video_translate`
- "看看我已经生成过哪些作品" -> `barry_video_manus_list`

## Publish

- "列出我能发 Facebook 的账号" -> `barry_video_publish_accounts`
- "把这个视频发到 Facebook" -> `barry_video_publish`
- "查一下发布任务完成没" -> `barry_video_publish_records`
- "把这个视频剪完直接发 Facebook" -> `barry_video_pipeline`
