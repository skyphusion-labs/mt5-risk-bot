# Live MT5 / MT4

Paper Docker on jello is the Telegram desk. It is not live execution.

Live execution needs a host that runs MetaTrader 4 or 5 and the Python
bot on the same OS. Official `MetaTrader5` is Windows. `mt5-mac` is macOS.
MT4 uses the Expert in `mt4/Experts/Mt4RiskBot.mq4` and `account.mode=mt4`.
Set `MT4_FILES_DIR` to Common Files. `doctor --connect` must print `venue=mt4`.
This fleet is Linux. Do not put Wine in the paper image and call it live.

## Path

1. Install MetaTrader 5. Log in. Enable AutoTrading.
2. Put secrets in a 0600 env file on that host. Never commit them.

```
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
AI_PROVIDER=computer
ADVICE_URL=https://mt5-risk-agent.skyphusion.workers.dev/ask
ADVICE_TOKEN=
POLL_SECONDS=1
```

3. Stop the paper container on jello. One bot token. One loop.

```
ssh jello 'cd /home/conrad/mt5-risk-bot && docker compose down'
```

4. On the MT5 host:

```
set -a && source .env && set +a
python -m mt5_risk_bot doctor --connect
python -m mt5_risk_bot run --mode mt5 --loop
```

On a Windows MT4 host, attach `Mt4RiskBot.mq4`. `ACCOUNT_MODE=mt4`.
`MT4_FILES_DIR` may be omitted (Common Files default). Then
`run --mode mt4 --loop`. See `docs/MT4.md`.

cmd.exe:

```
set ACCOUNT_MODE=mt4
set TELEGRAM_BOT_TOKEN=...
set TELEGRAM_CHAT_ID=...
python -m mt5_risk_bot doctor --connect
python -m mt5_risk_bot run --mode mt4 --loop
```

5. Demo (`trade_mode=0`) does not need `/live on`.
6. Real money (`trade_mode=2`): `/live on I-ACCEPT-RISK` then `/approve always`.

WARNING: Do not run paper on jello and live on another host with the same token.

## What live is not

- Not the `mt5-risk-bot:paper` image.
- Not dischord.
- Not a profit guarantee.
