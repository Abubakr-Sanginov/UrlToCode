# The database

Accounts, the projects they own, and what each of them may still spend are
kept in one place. Which place is a setting, not a decision baked into the
code:

| `DATABASE_URL` | Where the data goes |
| --- | --- |
| blank | `backend/data/accounts.db`, a local SQLite file |
| `postgresql://...` | Postgres — a Neon database |

Nothing else changes. `backend/db.py` is the only file that knows which one
answered, and it says so in the startup line:

```
Storage: sqlite data\accounts.db
```

That line is the first thing to check when data goes missing. A server left
running on the local file while a deployment believes it is on Neon loses
everything written by whichever of them started first.

## Turning it on for Neon

1. In the Neon console, create a project and **a branch** (Neon branches are
   separate databases; the default `main` is fine for this).
2. Under **Connect**, copy the connection string. It looks like
   `postgresql://user:password@ep-xxx.region.aws.neon.tech/dbname?sslmode=require`.
3. Put it in `backend/.env`:

   ```
   DATABASE_URL=postgresql://user:password@ep-xxx.region.aws.neon.tech/dbname?sslmode=require
   ```

4. Restart the backend. **A full restart, not a reload** — the value is read
   once when the process starts.

The four tables are created on that first start, so there is no migration to
run and nothing to remember to repeat.

The startup line then reads:

```
Storage: postgres ep-xxx.region.aws.neon.tech (user user)
```

It never prints the password. A Neon connection string carries it in the URL,
and a startup line ends up in logs and screenshots.

## What lives where

```
accounts          email, password hash, tier
projects          owner_id, run_id, name, source_url, created_at
usage             owner_id, kind, day, spent        -- daily allowance
limit_overrides   owner_id, daily_actions, max_projects, note
```

`projects` is the one you asked for: one row per cloned site, written the
moment a clone finishes, so a project's code can be opened again later
without paying to clone it a second time.

## The two dialects

The code above `db.py` is written once and runs on either engine. Three
differences are translated rather than remembered:

* **Placeholders.** Queries are written with `?` because that is what SQLite
  wants and what reads most clearly; `db.q` turns them into `%s` for Postgres.
* **New row ids.** SQLite reports one through `cursor.lastrowid`. Postgres has
  no such thing — the id comes back only if the INSERT says `RETURNING id`,
  which `Database.insert` writes for you.
* **Table definitions.** SQLite takes `INTEGER PRIMARY KEY AUTOINCREMENT`;
  Postgres has no rowid and needs `GENERATED ALWAYS AS IDENTITY`. Timestamps
  are `DOUBLE PRECISION` rather than `REAL`, which in Postgres is a float too
  narrow to hold a unix time with its fractional part.

`backend/tests/test_database_seam.py` checks all of that, and the SQLite half
of the schema is run against a real engine rather than a mock — so a typo
there shows up in the test run rather than on first signup.

## Connecting by hand

Neon ships a web SQL editor on the dashboard, which is enough to look at rows
while checking a deployment. From a terminal, `psql` takes the same URL.

Note that the pooled connection string and the direct one look alike but are
different hosts. Either works here; the pool this server opens is five
connections wide, well inside what Neon's free plan allows.