# Barry Video

`barry-video` is an OpenClaw plugin package for Barry's full Inbeidou workflow.

It packages two layers together:

- Skills: natural-language routing and workflow guidance under [`skills/`](./skills)
- Tools: real executable capability registered in [`index.ts`](./index.ts)

## Detailed spec

The full implementation and packaging spec is documented in [`docs/barry-video-spec.md`](./docs/barry-video-spec.md).

## What this package includes

- Account info
- Credit balance
- AI product pricing
- Translation language catalogs
- Short drama discovery for Dramabox and similar platforms
- Media library upload/list/delete
- Smart analysis
- Smart clipping
- Video translation
- Generated works list/detail/download/delete
- Social account listing
- Social publish create/records/delete
- One-shot clip -> download -> publish pipeline
- Raw backend passthrough for future CLI commands

## Repository layout

```text
barry-video/
├── index.ts
├── openclaw.plugin.json
├── package.json
├── bin/
├── scripts/
└── skills/
```

## How it works

1. `openclaw.plugin.json` tells OpenClaw this package provides a plugin and bundled skills.
2. `package.json` exposes the package as an installable OpenClaw extension.
3. `index.ts` registers tool-use actions that shell into the working backend at `inbeidou_cli.py`.
4. `skills/` tells the model when to use which tool based on natural language.
5. `scripts/install-local.sh` installs the package into `~/.openclaw`, prefers copying a local backend snapshot when available, and otherwise falls back to the bundled backend shipped inside the npm package.

## Local install

```bash
cd /Users/ming/barry-video
./scripts/install-local.sh
```

Optional environment variables for install:

```bash
export BARRY_VIDEO_BACKEND="$HOME/inbeidou_cli.py"
export INBEIDOU_TOKEN="your-token"
export BARRY_VIDEO_DEFAULT_ACCOUNT_IDS="109,108"
export BARRY_VIDEO_DEFAULT_PUBLISH_PLATFORM="FACEBOOK"
```

If you are installing on a different runtime user or machine, set `INBEIDOU_TOKEN` or `BARRY_VIDEO_AUTH_TOKEN` so the bundled backend can authenticate without depending on a local hardcoded script.

## Smoke test

```bash
cd /Users/ming/barry-video
./scripts/smoke-test.sh
```

## Package for distribution

```bash
cd /Users/ming/barry-video
./scripts/package-release.sh
```

## Publish to your own GitHub

```bash
cd /Users/ming/barry-video
git init
git add .
git commit -m "Initial Barry Video OpenClaw package"
git branch -M main
git remote add origin git@github.com:a77ming/barry-video.git
git push -u origin main
```

## Publish to npm later

If you want OpenClaw to install it with npm, publish this package and then keep the same package name or update `openclaw.install.npmSpec` in [`package.json`](./package.json).

```bash
npm login
npm publish --access public
```
