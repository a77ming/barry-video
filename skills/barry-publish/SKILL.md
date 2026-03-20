---
name: barry-publish
description: Use Barry Publish when the user wants to inspect authorized social accounts, publish a post or video, check publish records, delete publish tasks, or clip a video and publish it in one workflow.
---

# Barry Publish

Use these tools:

- `barry_video_publish_accounts`
- `barry_video_publish`
- `barry_video_publish_records`
- `barry_video_publish_delete`
- `barry_video_pipeline`

Workflow:

1. If no account or team target is known, call `barry_video_publish_accounts`.
2. For direct posting, call `barry_video_publish`.
3. For clip then publish, prefer `barry_video_pipeline`.
4. After posting, call `barry_video_publish_records` if the user asks whether it finished.
