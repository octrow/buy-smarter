**English** | **[Русский](CONTRIBUTING.ru.md)**

# Contributing

Bug reports, new categories of brand knowledge and source fixes are welcome.

## Setup

```bash
uv sync --extra server
make check   # ruff check + ruff format --check + pytest
```

Tests need no network and no database server: the suite runs on a tmp SQLite store. API, worker and job
tests need the server extra (`uv sync --extra server`) and are skipped without it. API and worker tests use fakeredis and an in-memory broker, so Redis is optional.

## Pull requests

- One change per PR, with a test that fails without it.
- Parser changes come with a saved response in `tests/samples/` (strip personal data first).
- Commit messages: `<type>: <description>`, where type is one of feat, fix, refactor, docs, test, chore, perf, ci.
- Marketplace adapters live in [market-sources](https://github.com/octrow/market-sources); a broken
  Kaspi/Ozon/OLX parser is usually fixed there.
- Changes to `README.md` go into `README.ru.md` too, and the other way round.
- A deliberate shortcut gets a `# ponytail: <limit>; <when to revisit>` comment naming the real-run event that
  should bring it back. [PONYTAIL-DEBT.md](PONYTAIL-DEBT.md) collects them; update it when you add or remove one.

Be polite to the marketplaces: keep request pacing as it is and don't add bulk crawling.
