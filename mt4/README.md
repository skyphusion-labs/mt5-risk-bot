# MT4 Expert

MetaTrader 4 has no official Python API.
The bot talks to this Expert through a file mailbox in Common Files.

## Install

1. Copy `Experts/Mt4RiskBot.mq4` into the terminal `MQL4/Experts` folder.
2. Compile it in MetaEditor.
3. Attach `Mt4RiskBot` to one chart. One chart is enough. The mailbox is global.
4. Enable AutoTrading. Allow live trading on the Expert.
5. Set `mt4.files_dir` (or `MT4_FILES_DIR`) to the Common Files folder:

```
%APPDATA%\MetaQuotes\Terminal\Common\Files
```

On Wine, that path is under the Wine prefix.

6. `account.mode = "mt4"`.
7. `python -m mt5_risk_bot --config config.toml doctor --connect`
8. Stop if doctor is not 0.
9. `python -m mt5_risk_bot --config config.toml run --mode mt4 --loop`

Demo (`trade_mode=0`) does not need `--i-accept-risk`.
Real money (`trade_mode=2`) needs `--i-accept-risk` or `/live on I-ACCEPT-RISK`.

See `docs/MT4.md` for the mailbox ICD.
