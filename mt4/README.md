# MT4 Expert

MetaTrader 4 has no official Python API.
The bot talks to this Expert through a file mailbox in Common Files.

## Install

1. Copy `Experts/Mt4RiskBot.mq4` into the terminal `MQL4/Experts` folder.
2. Compile it in MetaEditor.
3. Attach `Mt4RiskBot` to one chart. The mailbox is global under Common Files.
   You do not have to remember this one: a second instance in the same terminal
   refuses to initialise and says why in the Experts log, and every request is
   claimed by rename before it is read, so a duplicate attach cannot double an
   order. See `docs/MT4.md`, "One Expert, enforced".
4. Enable AutoTrading. Allow live trading on the Expert.
5. Set `mt4.files_dir` (or `MT4_FILES_DIR`) to the Common Files folder.
   On Windows you can omit it. The bot uses:

```
%APPDATA%\MetaQuotes\Terminal\Common\Files
```

On Wine, that path is under the Wine prefix. Set it by hand.

6. `account.mode = "mt4"`.
7. `python -m straightedge --config config.toml doctor --connect`
8. Stop if doctor is not 0.
9. `python -m straightedge --config config.toml run --mode mt4 --loop`

cmd.exe:

```
set ACCOUNT_MODE=mt4
set TELEGRAM_BOT_TOKEN=...
set TELEGRAM_CHAT_ID=...
python -m straightedge --config config.toml doctor --connect
python -m straightedge --config config.toml run --mode mt4 --loop
```

Demo (`trade_mode=0`) does not need `--i-accept-risk`.
Real money (`trade_mode=2`) needs `--i-accept-risk` or `/live on I-ACCEPT-RISK`.

See `docs/MT4.md` for the mailbox ICD.
