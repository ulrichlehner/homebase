# Changelog

## [1.5.0](https://github.com/ulrichlehner/homebase/compare/v1.4.0...v1.5.0) (2026-10-15)


### Features

* reception gaps of the live meter are split by its power readings ([#44](https://github.com/ulrichlehner/homebase/issues/44)) ([6338e8f](https://github.com/ulrichlehner/homebase/commit/6338e8f7ffd18f6587cd99f3fb3162825181c3de))
* the portal replaces live hours estimated across a reception gap ([#43](https://github.com/ulrichlehner/homebase/issues/43)) ([901099a](https://github.com/ulrichlehner/homebase/commit/901099a22a48062bbbb0b5ff2e97419a311eb19f))

## [1.4.0](https://github.com/ulrichlehner/homebase/compare/v1.3.1...v1.4.0) (2026-10-15)


### Features

* the add-on page lists gaps in the statistic ([#39](https://github.com/ulrichlehner/homebase/issues/39)) ([d91f2fe](https://github.com/ulrichlehner/homebase/commit/d91f2fe007333e92ae17931ff388af5b77acfc49))


### Bug Fixes

* add-on description ends with a period ([#42](https://github.com/ulrichlehner/homebase/issues/42)) ([83f96b2](https://github.com/ulrichlehner/homebase/commit/83f96b2899755e901c7cc9421f168bfee279b9d5))
* an upload during a run no longer crashes the add-on ([#41](https://github.com/ulrichlehner/homebase/issues/41)) ([e7eaba2](https://github.com/ulrichlehner/homebase/commit/e7eaba20b05e19d1cc3435503914ed3a47b6a6e8))

## [1.3.1](https://github.com/ulrichlehner/homebase/compare/v1.3.0...v1.3.1) (2026-10-15)


### Bug Fixes

* buttons on the add-on page return to the page instead of "Not Found" ([#37](https://github.com/ulrichlehner/homebase/issues/37)) ([3dedc71](https://github.com/ulrichlehner/homebase/commit/3dedc71397e1ea333964c0e0e41dd0fce1660a9f))
* password steps explain saving secrets.yaml and the one-minute delay ([#36](https://github.com/ulrichlehner/homebase/issues/36)) ([c55efd7](https://github.com/ulrichlehner/homebase/commit/c55efd7a0c6aece70af08e167621b75c87d93dfc))

## [1.3.0](https://github.com/ulrichlehner/homebase/compare/v1.2.0...v1.3.0) (2026-10-15)


### Features

* fetch the portal when a new day can be there, no interval option ([#35](https://github.com/ulrichlehner/homebase/issues/35)) ([a556cae](https://github.com/ulrichlehner/homebase/commit/a556cae20b448a8c8baf24e851838686e0cee3d7))
* live meter only on the add-on page, fetch button, clearer page ([#34](https://github.com/ulrichlehner/homebase/issues/34)) ([39e6418](https://github.com/ulrichlehner/homebase/commit/39e6418eb14f40cfbafada161b2f96085ad58492))
* warn while the portal password is not a !secret ([#32](https://github.com/ulrichlehner/homebase/issues/32)) ([d73cfe1](https://github.com/ulrichlehner/homebase/commit/d73cfe1d747b245477e41c4139c5e7184db106d0))


### Bug Fixes

* option names and help texts show again ([#31](https://github.com/ulrichlehner/homebase/issues/31)) ([7dd7793](https://github.com/ulrichlehner/homebase/commit/7dd7793fdcb1e4a8df2890a61c0bc0674de26dc3))

## [1.2.0](https://github.com/ulrichlehner/homebase/compare/v1.1.0...v1.2.0) (2026-10-15)


### Features

* AppArmor profile in complain mode ([#29](https://github.com/ulrichlehner/homebase/issues/29)) ([a405e6b](https://github.com/ulrichlehner/homebase/commit/a405e6b32e2a46e833ad23b0b099037dfefaaacd))

## [1.1.0](https://github.com/ulrichlehner/homebase/compare/v1.0.0...v1.1.0) (2026-10-15)


### Features

* pick the live meter on the add-on page ([#27](https://github.com/ulrichlehner/homebase/issues/27)) ([2212448](https://github.com/ulrichlehner/homebase/commit/2212448c38f9887e6a51565c6bad2a74674398d9))

## [1.0.0](https://github.com/ulrichlehner/homebase/compare/v0.2.2...v1.0.0) (2026-10-15)


### Features

* radio readings as primary source, MQTT status, Energy dashboard card, 1.0.0 (steps 6 to 8) ([#23](https://github.com/ulrichlehner/homebase/issues/23)) ([787f5bc](https://github.com/ulrichlehner/homebase/commit/787f5bc917994fb91ba49dcd597c114264cf7057))

## [0.2.2](https://github.com/ulrichlehner/homebase/compare/v0.2.1...v0.2.2) (2026-10-14)


### Bug Fixes

* add-on page scrolls in the Home Assistant app on iOS ([3c38ca4](https://github.com/ulrichlehner/homebase/commit/3c38ca43c1aedb7d9bdfa48f02d82e408b34054e))

## [0.2.1](https://github.com/ulrichlehner/homebase/compare/v0.2.0...v0.2.1) (2026-10-14)


### Bug Fixes

* add-on name "LINZ NETZ Energy", note that it is not affiliated with LINZ NETZ ([5ea195b](https://github.com/ulrichlehner/homebase/commit/5ea195bbfe61eb9a7a3e6baa3dd24de16222314e))
* add-on page fits phone screens ([dc6655f](https://github.com/ulrichlehner/homebase/commit/dc6655fd4865fec6d5d9edb9b804d587b86744f2))
* site is a free-text name, statistic names in the language of HA ([d5ba135](https://github.com/ulrichlehner/homebase/commit/d5ba135d39c11e485a48a26c58c6dac53f7a9e70))

## [0.2.0](https://github.com/ulrichlehner/homebase/compare/v0.1.1...v0.2.0) (2026-10-14)


### Features

* add-on texts in the language of Home Assistant, clearer upload ([fa7b6a2](https://github.com/ulrichlehner/homebase/commit/fa7b6a21bb8f4d56cb18fc5eb9ffdb265903e49f))

## [0.1.1](https://github.com/ulrichlehner/homebase/compare/v0.1.0...v0.1.1) (2026-10-14)


### Bug Fixes

* guide through the add-on setup instead of failing the start ([7785cf0](https://github.com/ulrichlehner/homebase/commit/7785cf058b454b944c2b08cf63fcd651c3ff4667))

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


### Bug Fixes

* interpolate RF across silences of up to 60 minutes ([c3839cc](https://github.com/ulrichlehner/homebase/commit/c3839ccf495341b2c89ee1effc6fd5a3ae1faace))
* the first-download notification points to the add-on page for older history ([e2a43fa](https://github.com/ulrichlehner/homebase/commit/e2a43fa59e22c39f5d9a4df7ff64c91b941570c7))
* valid YAML in the add-on image workflow ([995e14b](https://github.com/ulrichlehner/homebase/commit/995e14b2b165711c02dff0250c7fcde5f72f1958))
* wait for the asynchronous statistics import before reading back ([c45c50e](https://github.com/ulrichlehner/homebase/commit/c45c50e34423b6f7538ffb7cb0cba14286e960fb))
