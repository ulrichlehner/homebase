# Changelog

## 0.1.0 (2026-10-14)


### Features

* add-on page in Home Assistant with status and history upload ([4d29c8d](https://github.com/ulrichlehner/homebase/commit/4d29c8dd40ecd19955ea112dc1070cbc6e86d96b))
* announce and report the first portal download with a notification ([013f5a6](https://github.com/ulrichlehner/homebase/commit/013f5a679ff6c53d61aa69d50eb3fb139086df57))
* cleaner add-on icon, bars and bolt side by side ([aeac5cf](https://github.com/ulrichlehner/homebase/commit/aeac5cfcf80d545162419a39ceab53276f1dc960))
* final names before the first install ([5c0b67a](https://github.com/ulrichlehner/homebase/commit/5c0b67a9e4b221a5fad7457a0b82fe0c359526fe))
* fuse portal, RF and reconstructed readings into one hourly series ([197fe0f](https://github.com/ulrichlehner/homebase/commit/197fe0f02096870f1434264474047b6ff6b1020c))
* Home Assistant add-on with the portal statistic ([c024718](https://github.com/ulrichlehner/homebase/commit/c024718102af4eaf67c9e9aecaa09f94243c0d51))
* Home Assistant websocket client and statistics import spike ([2e2245c](https://github.com/ulrichlehner/homebase/commit/2e2245cf98b9778b55ba0f7cfb8afebc2a6a2b41))
* icon, logo, changelog and store metadata for the add-on ([711ba69](https://github.com/ulrichlehner/homebase/commit/711ba696677840a36f6a28fb7f28afd90489af06))
* import older history into the add-on, fused statistic ([97ba026](https://github.com/ulrichlehner/homebase/commit/97ba026af3efbc8c0b0045b49f3d566d00e9285e))
* rename the add-on option to fetch_interval_hours, German translation ([9b96e05](https://github.com/ulrichlehner/homebase/commit/9b96e05be6ce1467ecf66bbe7cc1b771a326c059))
* RF readings are the primary source, the portal verifies and fills gaps ([8a9b9c3](https://github.com/ulrichlehner/homebase/commit/8a9b9c3e8289d1fcd3e2ac17d97c91fe879c2132))
* split the reconstructed 6h blocks into quarter hours ([ec4f78f](https://github.com/ulrichlehner/homebase/commit/ec4f78fb4fb8fc3a21517c259fc3514e6d8cbf39))
* split the reconstructed 6h blocks into quarter hours (WIP) ([a22544e](https://github.com/ulrichlehner/homebase/commit/a22544e731e2a66216e4da6d0fdb3dd72d4166d5))


### Bug Fixes

* interpolate RF across silences of up to 60 minutes ([c3839cc](https://github.com/ulrichlehner/homebase/commit/c3839ccf495341b2c89ee1effc6fd5a3ae1faace))
* the first-download notification points to the add-on page for older history ([e2a43fa](https://github.com/ulrichlehner/homebase/commit/e2a43fa59e22c39f5d9a4df7ff64c91b941570c7))
* valid YAML in the add-on image workflow ([995e14b](https://github.com/ulrichlehner/homebase/commit/995e14b2b165711c02dff0250c7fcde5f72f1958))
* wait for the asynchronous statistics import before reading back ([c45c50e](https://github.com/ulrichlehner/homebase/commit/c45c50e34423b6f7538ffb7cb0cba14286e960fb))

## Changelog

Written by release-please from the conventional commits (see the README, "Releases").
