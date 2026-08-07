# Parking Reminder — Backend

A Node.js + TypeScript backend that exposes a small REST API for recording
parking locations and ships with a Discord bot for `/whereiscar`, `/park`,
and `/parkgps` slash commands. Data is persisted to a local SQLite database
via Prisma.

---

## Quick Start

```bash
# 1. Install dependencies
npm install

# 2. Configure secrets
cp .env.example .env
# edit .env and fill in DISCORD_TOKEN / DISCORD_CLIENT_ID

# 3. Generate the Prisma client and create the SQLite DB
npm run prisma:generate
npm run prisma:push

# 4. Start the dev server (REST API + bot together)
npm run dev
```

For production:

```bash
npm run build      # compiles to ./dist
npm start          # runs the compiled server
```

The SQLite database lives at `./data/parking.db` and is created automatically
by `prisma db push`.

---

## REST API

| Method | Path                       | Purpose                                         |
| :----- | :------------------------- | :---------------------------------------------- |
| GET    | `/api/health`              | Liveness probe                                  |
| POST   | `/api/park`                | Create a new parking record                     |
| GET    | `/api/park/latest`         | Latest record for a user                        |
| GET    | `/api/park/history`        | Last N records (default 10, max 50)             |
| DELETE | `/api/park/:id`            | Delete a specific record                        |

### POST /api/park

Indoor:

```json
{
  "floor": "B3",
  "slot": "27",
  "is_indoor": true,
  "user_id": "default"
}
```

Outdoor (GPS):

```json
{
  "latitude": 25.03,
  "longitude": 121.57,
  "is_indoor": false,
  "user_id": "default"
}
```

Response:

```json
{
  "id": "uuid",
  "status": "saved",
  "timestamp": "2025-01-01T00:00:00.000Z",
  "location_note": "B3-27"
}
```

### GET /api/park/latest?user_id=default

Returns the serialized record (including the computed `location_note`).
`location_note` is `"<floor>-<slot>"` for indoor records and a Google
Maps URL for outdoor records.

### GET /api/park/history?user_id=default&limit=10

`limit` is clamped to `1..50`; default is `10`.

---

## Discord Bot

Slash commands (per-user; keyed on Discord snowflake):

- `/whereiscar` — Reply with your most recent parking record (indoor
  format or Google Maps link).
- `/park floor:<B3> slot:<27>` — Save an indoor record.
- `/parkgps latitude:<25.03> longitude:<121.57>` — Save an outdoor
  record; replies with a Google Maps link.

Setting `DISCORD_GUILD_ID` in `.env` registers commands to a single
guild (instant updates — recommended for development). Leaving it
empty registers globally (takes up to ~1 hour to propagate).

---

## Project Layout

```
backend/
  src/
    index.ts              # Express bootstrap + bot lifecycle
    routes/parking.ts     # /api/park* endpoints
    bot/
      index.ts            # Discord client + command registration
      register-commands.ts# Standalone registration helper
      commands/
        whereiscar.ts
        park.ts
        parkgps.ts
    lib/
      prisma.ts           # Shared Prisma client (singleton)
      service.ts          # Shared DB access helpers
      parking.ts          # locationNote + relative-time helpers
    generated/            # Prisma client output (gitignored)
  prisma/schema.prisma    # Database schema
  data/                   # SQLite DB files (gitignored)
```

---

## Environment Variables

| Variable             | Required | Description                                  |
| :------------------- | :------: | :------------------------------------------- |
| `DISCORD_TOKEN`      | Bot only | Bot token from Discord developer portal     |
| `DISCORD_CLIENT_ID`  | Bot only | Application / client ID                       |
| `DISCORD_GUILD_ID`   | No       | Restrict commands to one guild (faster sync)  |
| `PORT`               | No       | HTTP port (default `3000`)                    |
| `DATABASE_URL`       | Yes      | Prisma SQLite URL (default `file:./data/...`)|

The REST API server starts even if Discord credentials are missing — it
will simply log a warning and skip the bot.
