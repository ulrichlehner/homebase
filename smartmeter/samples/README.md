# Real telegrams of the LINZ NETZ meter

Captured with the wmbusmeters add-on in Home Assistant (`logtelegrams: true`, RTL-SDR, T1 mode),
2026-10-12 11:43 to 2026-10-16 13:16 (meter time), one telegram every 5 s: 68,725 telegrams.
They are the material for the [driver](../drivers/amiplus_linznetz.xmq) and for the
[LINZ NETZ Energy add-on](../../linznetz_energy), which splits the reception gaps by power.

| File | Content |
| --- | --- |
| `2026-10-12_16_telegrams.txt.gz` | every telegram, one per line, anonymised and unencrypted: runs through wmbusmeters with `NOKEY` |
| `2026-10-12_16_records.csv.gz` | the same telegrams decoded: meter time, import and export VIF and digits, power and export power in W |
| [`../tests/telegrams_real.txt`](../tests/telegrams_real.txt) | the two telegrams on each side of every switch between record kinds, with the expected kWh; part of `tests/run.sh` |

## What was changed, what was not

wmbusmeters logs each telegram already decrypted (`telegram=|<header>_<payload>|`).
[`../tools/telegram_log.py`](../tools/telegram_log.py) keeps only those lines (the add-on's
configuration lines with the key are never read) and replaces:

- the meter id in the header and the fabrication number (`0C 78` record) with the fantasy id
  `12345678`,
- the encryption mode in the header with "none", dropping the `2F 2F` check bytes and fixing the
  length, so that wmbusmeters reads the telegram without a key.

Meter time, access number, counters and power are as received. Telegrams of other meters in
range are dropped (there were none in this capture).

```bash
python3 smartmeter/tools/telegram_log.py phases LOG      # the report below
python3 smartmeter/tools/telegram_log.py telegrams LOG smartmeter/samples/<period>_telegrams.txt.gz
python3 smartmeter/tools/telegram_log.py records LOG smartmeter/samples/<period>_records.csv.gz
python3 smartmeter/tools/telegram_log.py samples LOG smartmeter/tests/telegrams_real.txt
```

`LOG` is the add-on log: in Home Assistant under Settings → Add-ons → wmbusmeters → Log →
download, or with an admin token `GET /api/hassio/addons/<slug>/logs?lines=2000000`. Check the
output for the real meter id (also byte-reversed), the fabrication number and the key before
committing.

## Findings (2026-10-12 to 2026-10-16)

| Records (import / export) | Telegrams | Share |
| --- | --- | --- |
| `0E 03` / `0E 83 3C` (spec, Wh) | 56,813 | 82.7 % |
| `0E 00` / `0E 80 3C`, all zero | 7,540 | 11.0 % |
| `0E 07` / `0E 87 3C` | 1,845 | 2.7 % |
| `0E 05` / `0E 85 3C` | 1,128 | 1.6 % |
| `0E 04` / `0E 84 3C` | 1,077 | 1.6 % |
| `0E 06` / `0E 86 3C` | 322 | 0.5 % |

- 32 phases of non-spec records, 22 to 52 minutes each, several a day. Each phase uses one
  record kind for import and export alike.
- What the digits were, against the last spec counter before the phase:
  - all zero: 21 phases,
  - the counter in Wh with a wrong VIF (`04`, `05`, `06`, `07`): 8 phases, continuing without a
    step,
  - the counter in tenths of a Wh with VIF `07`: 3 phases (12.10. and 13.10.); e.g. digits
    `1183890` where the counter stood at 118,389 Wh,
  - digits really in the unit of their VIF: none in this capture (seen on 9.10. and 11.10.
    through the old driver, see the [README](../README.md#wrong-energy-values-and-the-custom-driver)).
- Since 15.10. 05:27 every phase was all zero.
- Spec counter: import 118,276 to 158,538 Wh, never decreasing, no jumps; export unchanged
  (no PV), export power always 0 W; power up to 7,056 W.
- Power records (`0B 2B`, `0B AB 3C`) were present in every telegram, also during the phases.
- Reception: the median interval is 5 s; gaps over 2 minutes only at add-on restarts (2 to
  2.5 min) and during the setup on 12.10. (up to 15 min).

The driver gives the expected kWh for every spec telegram and no value for every other one
(`tests/run.sh`, one telegram per run). Run over all 68,725 telegrams at once, as the add-on does,
wmbusmeters repeats the last spec value during a phase; nothing else differs. Home Assistant's
history shows the same: the energy sensors stay flat during a phase, no `unknown`.

Non-spec phases:

| Start (meter time) | Minutes | Import record | Telegrams | Digits |
| --- | --- | --- | --- | --- |
| 2026-10-12 12:07:15 | 26.5 | `0E 07` | 318 | tenths of Wh, wrong VIF |
| 2026-10-12 14:14:20 | 36.3 | `0E 00` | 434 | all zero |
| 2026-10-12 17:16:20 | 28.1 | `0E 07` | 336 | tenths of Wh, wrong VIF |
| 2026-10-12 19:47:45 | 51.6 | `0E 04` | 610 | Wh digits, wrong VIF |
| 2026-10-12 23:21:50 | 29.9 | `0E 00` | 357 | all zero |
| 2026-10-13 00:51:15 | 32.4 | `0E 00` | 385 | all zero |
| 2026-10-13 03:32:55 | 35.5 | `0E 07` | 420 | Wh digits, wrong VIF |
| 2026-10-13 04:41:10 | 30.5 | `0E 05` | 364 | Wh digits, wrong VIF |
| 2026-10-13 06:26:35 | 32.5 | `0E 00` | 387 | all zero |
| 2026-10-13 09:27:10 | 25.1 | `0E 00` | 301 | all zero |
| 2026-10-13 15:01:05 | 29.5 | `0E 00` | 348 | all zero |
| 2026-10-13 19:16:20 | 31.2 | `0E 07` | 369 | tenths of Wh, wrong VIF |
| 2026-10-13 20:23:30 | 28.6 | `0E 00` | 342 | all zero |
| 2026-10-13 23:27:35 | 27.2 | `0E 06` | 322 | Wh digits, wrong VIF |
| 2026-10-14 07:56:55 | 29.2 | `0E 00` | 348 | all zero |
| 2026-10-14 10:51:05 | 32.2 | `0E 05` | 386 | Wh digits, wrong VIF |
| 2026-10-14 11:50:05 | 29.8 | `0E 00` | 356 | all zero |
| 2026-10-14 16:56:10 | 33.2 | `0E 00` | 393 | all zero |
| 2026-10-14 21:06:35 | 29.9 | `0E 00` | 357 | all zero |
| 2026-10-14 23:19:10 | 31.8 | `0E 05` | 378 | Wh digits, wrong VIF |
| 2026-10-15 00:24:35 | 34.0 | `0E 07` | 402 | Wh digits, wrong VIF |
| 2026-10-15 02:08:00 | 39.0 | `0E 04` | 467 | Wh digits, wrong VIF |
| 2026-10-15 05:27:45 | 33.5 | `0E 00` | 395 | all zero |
| 2026-10-15 09:27:45 | 26.0 | `0E 00` | 312 | all zero |
| 2026-10-15 11:20:30 | 36.4 | `0E 00` | 433 | all zero |
| 2026-10-15 13:31:40 | 28.2 | `0E 00` | 338 | all zero |
| 2026-10-15 18:27:15 | 36.9 | `0E 00` | 440 | all zero |
| 2026-10-15 21:26:40 | 32.7 | `0E 00` | 390 | all zero |
| 2026-10-16 03:00:40 | 25.2 | `0E 00` | 295 | all zero |
| 2026-10-16 09:29:00 | 34.1 | `0E 00` | 406 | all zero |
| 2026-10-16 10:38:10 | 22.0 | `0E 00` | 264 | all zero |
| 2026-10-16 12:55:01 | 21.6 | `0E 00` | 259 | all zero |
