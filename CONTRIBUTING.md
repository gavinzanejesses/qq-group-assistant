# Contributing

1. Create a virtual environment and install `.[dev]`.
2. Keep real QQ numbers, messages, tokens and member data out of fixtures.
3. Run `qq-group-assistant validate --config config.example.yml`, `pytest`, and `ruff check .` before opening a pull request.
4. New destructive operations must have an authorization check, an audit record and a safe default.
5. Describe OneBot/NapCat compatibility assumptions in the pull request.
