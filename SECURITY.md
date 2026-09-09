# Security

Report vulnerabilities to conrad@skyphusion.org. Do not open a public issue
for a live trading defect that could move money.

Secrets never belong in this repo. Account login, password, server, and
Telegram token are environment variables. `config.toml` is gitignored.

A real-money account is refused unless the process was started with
`--i-accept-risk`.
