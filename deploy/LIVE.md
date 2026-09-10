# Live MT5

Paper Docker on jello is the Telegram desk. It is not live execution.

Live execution needs a host that runs MetaTrader 5 and the Python bot
on the same OS. Official `MetaTrader5` is Windows. `mt5-mac` is macOS.
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

5. Demo (`trade_mode=0`) does not need `/live on`.
6. Real money (`trade_mode=2`): `/live on I-ACCEPT-RISK` then `/approve always`.

WARNING: Do not run paper on jello and live on another host with the same token.

## What live is not

- Not the `mt5-risk-bot:paper` image.
- Not dischord.
- Not a profit guarantee.
