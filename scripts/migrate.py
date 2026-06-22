import asyncio
import asyncpg
import os
import sys
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, 'api', '.env'))


async def migrate():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS premium_interest (
                id         SERIAL PRIMARY KEY,
                email      TEXT NOT NULL UNIQUE,
                mode       TEXT,
                trigger    TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        print("OK premium_interest table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS packs (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT,
                accent      TEXT NOT NULL,
                icon        TEXT,
                mode        TEXT NOT NULL DEFAULT 'party',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        print("OK packs table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id      TEXT PRIMARY KEY,
                pack_id TEXT NOT NULL REFERENCES packs(id),
                type    TEXT NOT NULL,
                text    TEXT NOT NULL,
                flavour TEXT
            )
        """)
        print("OK cards table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS venues (
                id                      UUID PRIMARY KEY,
                name                    TEXT NOT NULL,
                slug                    TEXT UNIQUE NOT NULL,
                venue_type              TEXT NOT NULL CHECK (venue_type IN ('cafe', 'pub', 'bar', 'brewery', 'other')),
                billing_unit            NUMERIC NOT NULL DEFAULT 3.00,
                round_time_minutes      INTEGER NOT NULL DEFAULT 20,
                retap_interval_minutes  INTEGER NOT NULL DEFAULT 30,
                nightly_cap_weekday     NUMERIC NOT NULL DEFAULT 30,
                nightly_cap_weekend     NUMERIC NOT NULL DEFAULT 30,
                nightly_cap_holiday     NUMERIC NOT NULL DEFAULT 30,
                stripe_customer_id      TEXT,
                menu_url                TEXT,
                restrict_adult_content  BOOLEAN DEFAULT FALSE,
                is_test                 BOOLEAN DEFAULT FALSE,
                status                  TEXT NOT NULL DEFAULT 'active',
                created_at              TIMESTAMP DEFAULT NOW(),
                updated_at              TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK venues table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id             UUID PRIMARY KEY,
                clerk_user_id  TEXT UNIQUE NOT NULL,
                venue_id       UUID REFERENCES venues(id),
                role           TEXT NOT NULL CHECK (role IN ('venue_owner', 'venue_staff', 'admin')),
                created_at     TIMESTAMP DEFAULT NOW(),
                -- admin = platform-wide (no venue); a venue_owner may have NO venue
                -- yet (just signed up -> setup wizard); staff always belong to a venue
                CHECK (
                    (role = 'admin' AND venue_id IS NULL)
                    OR (role = 'venue_owner')
                    OR (role = 'venue_staff' AND venue_id IS NOT NULL)
                )
            )
        """)
        print("OK users table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tables (
                id               UUID PRIMARY KEY,
                venue_id         UUID NOT NULL REFERENCES venues(id),
                table_number     INTEGER NOT NULL,
                content_ceiling  TEXT NOT NULL DEFAULT 'standard' CHECK (content_ceiling IN ('standard', 'adults_allowed')),
                created_at       TIMESTAMP DEFAULT NOW(),
                UNIQUE (venue_id, table_number)
            )
        """)
        print("OK tables table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS nfc_tags (
                id                  UUID PRIMARY KEY,
                venue_id            UUID NOT NULL REFERENCES venues(id),
                table_id            UUID REFERENCES tables(id),
                tag_uid             TEXT UNIQUE NOT NULL,
                aes_key_encrypted   TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'replacement_pending')),
                counter_last_seen   BIGINT,
                paired_at           TIMESTAMP,
                created_at          TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK nfc_tags table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS game_sessions (
                id                   UUID PRIMARY KEY,
                venue_id             UUID NOT NULL REFERENCES venues(id),
                table_id             UUID NOT NULL REFERENCES tables(id),
                group_label          TEXT,
                player_count         INTEGER NOT NULL,
                player_names         JSONB,
                adults_only          BOOLEAN DEFAULT FALSE,
                theme_key_at_start   TEXT,
                selection_mode       TEXT,

                started_at           TIMESTAMP,
                ended_at             TIMESTAMP,
                end_reason           TEXT,

                total_rounds         INTEGER DEFAULT 0,
                cards_completed      INTEGER DEFAULT 0,
                cards_skipped        INTEGER DEFAULT 0,
                trivia_correct       INTEGER DEFAULT 0,
                trivia_wrong         INTEGER DEFAULT 0,
                total_score          INTEGER DEFAULT 0,
                scores               JSONB,

                created_at           TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK game_sessions table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS game_players (
                id              UUID PRIMARY KEY,
                session_id      UUID NOT NULL REFERENCES game_sessions(id),
                name            TEXT NOT NULL,
                score           INTEGER DEFAULT 0,
                times_selected  INTEGER DEFAULT 0,
                left_early      BOOLEAN DEFAULT FALSE,
                left_at         TIMESTAMP
            )
        """)
        print("OK game_players table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bar_cards (
                id              UUID PRIMARY KEY,
                content         TEXT NOT NULL,
                type            TEXT NOT NULL CHECK (type IN (
                    'icebreaker', 'truth', 'dare', 'compliment', 'challenge', 'drink', 'flirty'
                )),
                is_adults_only  BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK bar_cards table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
                id                  UUID PRIMARY KEY,
                session_id          UUID NOT NULL REFERENCES game_sessions(id),
                round_number        INTEGER NOT NULL,
                round_type          TEXT NOT NULL,
                selected_player_id  UUID REFERENCES game_players(id),
                card_id             UUID,
                trivia_question_id  UUID,
                card_type           TEXT,
                trivia_category     TEXT,
                result              TEXT,
                score_awarded       INTEGER DEFAULT 0,
                time_to_answer      INTEGER,
                redraw_count        INTEGER DEFAULT 0,
                created_at          TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK rounds table ready")

        # ALTER rather than inside game_sessions' CREATE above, for two
        # reasons: (1) CREATE TABLE IF NOT EXISTS no-ops against the
        # already-existing dev/prod table, so a column added there would
        # never actually land; (2) last_hot_seat_player_id references
        # game_players, which doesn't exist yet when game_sessions is
        # created above — the two tables have a circular dependency
        # (game_players.session_id -> game_sessions, this -> game_players).
        # gamespec: "Players place fingers on session-origin phone" — the
        # phone that started the game is the one device the finger picker
        # runs on; tracked server-side so it can't be spoofed by another
        # phone at the table. last_hot_seat_player_id backs the "exclude
        # the previous winner" rule for 3+ players (Finger Picker).
        await conn.execute("""
            ALTER TABLE game_sessions
            ADD COLUMN IF NOT EXISTS origin_phone_id TEXT,
            ADD COLUMN IF NOT EXISTS last_hot_seat_player_id UUID REFERENCES game_players(id)
        """)
        print("OK game_sessions.origin_phone_id / last_hot_seat_player_id ready")

        await conn.execute("""
            ALTER TABLE game_sessions
            ADD COLUMN IF NOT EXISTS current_round_number INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS drink_disclaimer_shown BOOLEAN DEFAULT FALSE
        """)
        print("OK game_sessions.current_round_number / drink_disclaimer_shown ready")

        # Tracks the last time any player action occurred in a session so the
        # idle-expiry check in _check_phone_session_resume can compare against
        # retap_interval_minutes in SQL (avoiding naive-timestamp/tz bugs).
        await conn.execute("""
            ALTER TABLE game_sessions
            ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMP DEFAULT NOW()
        """)
        print("OK game_sessions.last_activity_at ready")

        # Not in gamespec.md's table list — gamespec describes lobby *behavior*
        # (Player Flow -> Step 2) without naming a table for it. This is the
        # implementation detail needed to track "who's tapped in, who's host"
        # before Setup completes and a real game_sessions row exists.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS table_lobbies (
                id                    UUID PRIMARY KEY,
                venue_id              UUID NOT NULL REFERENCES venues(id),
                table_id              UUID NOT NULL REFERENCES tables(id),
                status                TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'converted', 'expired')),
                host_phone_id         TEXT,
                converted_session_id  UUID REFERENCES game_sessions(id),
                created_at            TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK table_lobbies table ready")

        # Only one *open* lobby per table at a time — once converted/expired,
        # a fresh tap (or "start a new group") can open another.
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_lobby_per_table
            ON table_lobbies (table_id) WHERE status = 'open'
        """)
        print("OK one_open_lobby_per_table index ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS table_lobby_phones (
                id          UUID PRIMARY KEY,
                lobby_id    UUID NOT NULL REFERENCES table_lobbies(id),
                phone_id    TEXT NOT NULL,
                joined_at   TIMESTAMP DEFAULT NOW(),
                UNIQUE (lobby_id, phone_id)
            )
        """)
        print("OK table_lobby_phones table ready")

        await conn.execute("""
            ALTER TABLE table_lobby_phones
            ADD COLUMN IF NOT EXISTS name TEXT
        """)
        print("OK table_lobby_phones.name ready")

        # Bind each game_players row to the phone it represents. Needed by the
        # Trivia round (Round Type 2): every player answers on their own device,
        # so a phone's answer must score the right person (Scoring -> Discernibility
        # Principle). Populated at lobby start_game and join_existing_session.
        # Nullable so pre-existing rows and any non-phone player stay valid.
        await conn.execute("""
            ALTER TABLE game_players
            ADD COLUMN IF NOT EXISTS phone_id TEXT
        """)
        print("OK game_players.phone_id ready")

        # gamespec Round Type 2 -- Trivia. The question bank. correct_option is
        # server-side only and is NEVER sent to the browser before a phone answers
        # (security.md #97 / coding-practices MingleHub Rules).
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trivia_questions (
                id              UUID PRIMARY KEY,
                question        TEXT NOT NULL,
                option_a        TEXT NOT NULL,
                option_b        TEXT NOT NULL,
                option_c        TEXT NOT NULL,
                option_d        TEXT NOT NULL,
                correct_option  TEXT NOT NULL CHECK (correct_option IN ('A', 'B', 'C', 'D')),
                category        TEXT NOT NULL,
                is_adults_only  BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK trivia_questions table ready")

        # One trivia round per session-round: tracks the 5 chosen questions, the
        # gather/in-progress/complete lifecycle, and which question is live (with
        # the timestamp the 20s timer is measured from). Distinct from `rounds`
        # (one analytics row per round); a trivia round spans 5 questions x N phones.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trivia_rounds (
                id                           UUID PRIMARY KEY,
                session_id                   UUID NOT NULL REFERENCES game_sessions(id),
                status                       TEXT NOT NULL DEFAULT 'gathering'
                    CHECK (status IN ('gathering', 'in_progress', 'complete', 'abandoned_at_gather')),
                question_ids                 UUID[] NOT NULL,
                category                     TEXT,
                adults_only                  BOOLEAN DEFAULT FALSE,
                current_index                INTEGER NOT NULL DEFAULT 0,
                current_question_started_at  TIMESTAMP,
                created_at                   TIMESTAMP DEFAULT NOW(),
                started_at                   TIMESTAMP,
                ended_at                     TIMESTAMP
            )
        """)
        print("OK trivia_rounds table ready")

        # Only one active (gathering/in_progress) trivia round per session at a
        # time -- a second `start` while one is live is a no-op/conflict, not a
        # second row. Partial unique index, same pattern as one_open_lobby_per_table.
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_trivia_round_per_session
            ON trivia_rounds (session_id) WHERE status IN ('gathering', 'in_progress')
        """)
        print("OK one_active_trivia_round_per_session index ready")

        # Phones that joined a trivia round's gather. player_id binds the phone to
        # its game_players row so scoring lands on the right person.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trivia_participants (
                id               UUID PRIMARY KEY,
                trivia_round_id  UUID NOT NULL REFERENCES trivia_rounds(id),
                phone_id         TEXT NOT NULL,
                player_id        UUID NOT NULL REFERENCES game_players(id),
                joined_at        TIMESTAMP DEFAULT NOW(),
                UNIQUE (trivia_round_id, phone_id)
            )
        """)
        print("OK trivia_participants table ready")

        # One answer per phone per question. is_correct/before_timer/score_awarded
        # are all computed server-side (the client never sees correct_option until
        # after it has answered). The UNIQUE guard makes re-submits a no-op (409).
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trivia_answers (
                id                UUID PRIMARY KEY,
                trivia_round_id   UUID NOT NULL REFERENCES trivia_rounds(id),
                question_id       UUID NOT NULL REFERENCES trivia_questions(id),
                question_index    INTEGER NOT NULL,
                phone_id          TEXT NOT NULL,
                player_id         UUID NOT NULL REFERENCES game_players(id),
                selected_option   TEXT NOT NULL CHECK (selected_option IN ('A', 'B', 'C', 'D')),
                is_correct        BOOLEAN NOT NULL,
                before_timer      BOOLEAN NOT NULL,
                time_to_answer_ms INTEGER,
                score_awarded     INTEGER NOT NULL DEFAULT 0,
                answered_at       TIMESTAMP DEFAULT NOW(),
                UNIQUE (trivia_round_id, question_index, phone_id)
            )
        """)
        print("OK trivia_answers table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS roulette_cards (
                id                          UUID PRIMARY KEY,
                prompt_text                 TEXT NOT NULL,
                content_tier                TEXT NOT NULL DEFAULT 'standard'
                    CHECK (content_tier IN ('standard', 'adults_allowed')),
                drink_consequence_standard  TEXT NOT NULL,
                drink_consequence_adults    TEXT NOT NULL,
                created_at                  TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK roulette_cards table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS roulette_votes (
                id               UUID PRIMARY KEY,
                round_id         UUID NOT NULL REFERENCES rounds(id),
                voter_phone_id   TEXT NOT NULL,
                voted_player_id  UUID NOT NULL REFERENCES game_players(id),
                created_at       TIMESTAMP DEFAULT NOW(),
                UNIQUE (round_id, voter_phone_id)
            )
        """)
        print("OK roulette_votes table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS venue_config_overrides (
                id          UUID PRIMARY KEY,
                venue_id    UUID NOT NULL REFERENCES venues(id),
                field_name  TEXT NOT NULL,
                old_value   TEXT,
                new_value   TEXT,
                reason      TEXT NOT NULL,
                changed_by  UUID REFERENCES users(id),
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK venue_config_overrides table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS support_messages (
                id          UUID PRIMARY KEY,
                venue_id    UUID REFERENCES venues(id),
                name        TEXT,
                email       TEXT,
                message     TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK support_messages table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id          UUID PRIMARY KEY,
                name        TEXT,
                email       TEXT,
                phone       TEXT,
                venue_name  TEXT,
                source      TEXT,
                notes       TEXT,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK leads table ready")

        # Lightweight append-only tap log: every verified NFC tap inserts a row.
        # Used by POST /sessions/{id}/join as proof of physical presence (BOLA
        # hardening — a phone that never tapped the table cannot join a session).
        # No UNIQUE constraint — a phone can tap multiple times, all recorded.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS table_tap_log (
                id         UUID PRIMARY KEY,
                table_id   UUID NOT NULL REFERENCES tables(id),
                phone_id   TEXT NOT NULL,
                tapped_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK table_tap_log table ready")

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tap_log_table_phone
            ON table_tap_log (table_id, phone_id)
        """)
        print("OK idx_tap_log_table_phone index ready")

        from scripts.seed_bar_cards import seed as seed_bar_cards  # noqa: E402
        await seed_bar_cards(conn)
        print("OK bar_cards seeded")

        from scripts.seed_trivia_questions import seed as seed_trivia_questions  # noqa: E402
        await seed_trivia_questions(conn)
        print("OK trivia_questions seeded")

        from scripts.seed_roulette_cards import seed as seed_roulette_cards  # noqa: E402
        await seed_roulette_cards(conn)
        print("OK roulette_cards seeded")

        # Re-Tap to Continue: lower the default idle threshold from 30 to 15 minutes.
        # Idempotent: SET DEFAULT always succeeds; the UPDATE only touches rows
        # still at the old default (venues whose admin explicitly set a different
        # value are left alone).
        await conn.execute("""
            ALTER TABLE venues ALTER COLUMN retap_interval_minutes SET DEFAULT 15
        """)
        await conn.execute("""
            UPDATE venues SET retap_interval_minutes = 15
            WHERE retap_interval_minutes = 30
        """)
        print("OK venues.retap_interval_minutes default changed 30 -> 15")

        # Onboarding: relax users_check so a venue_owner may have NO venue yet (just
        # signed up via Clerk -> the venue-setup wizard fills it in). admin stays
        # NULL-only; staff must belong to a venue.
        await conn.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_check")
        await conn.execute("""
            ALTER TABLE users ADD CONSTRAINT users_check CHECK (
                (role = 'admin' AND venue_id IS NULL)
                OR (role = 'venue_owner')
                OR (role = 'venue_staff' AND venue_id IS NOT NULL)
            )
        """)
        print("OK users_check relaxed: venue_owner may have NULL venue (pending setup)")

        # Onboarding: venue address (Photon/OSM autocomplete) — formatted string + coords.
        await conn.execute("""
            ALTER TABLE venues
            ADD COLUMN IF NOT EXISTS address TEXT,
            ADD COLUMN IF NOT EXISTS latitude NUMERIC,
            ADD COLUMN IF NOT EXISTS longitude NUMERIC,
            ADD COLUMN IF NOT EXISTS place_id TEXT
        """)
        print("OK venues address columns ready")

        # Usage billing: per-session play measures, frozen at session end.
        #  - active_play_seconds: true play time (idle gaps > 2 min excluded) -- analytics.
        #  - active_span_seconds: started_at -> last_activity_at (the dead idle tail
        #    before an auto-end is NOT counted) -- the billing basis.
        #  - billable_blocks: floor(active_span / 15 min), but only if >=1 round was
        #    actually played (resolved). One block = one billing_unit ($3).
        #  - billing_finalized_at: set once when frozen -> makes finalize idempotent.
        await conn.execute("""
            ALTER TABLE game_sessions
            ADD COLUMN IF NOT EXISTS active_play_seconds  INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS active_span_seconds  INTEGER,
            ADD COLUMN IF NOT EXISTS billable_blocks      INTEGER,
            ADD COLUMN IF NOT EXISTS billing_finalized_at TIMESTAMP
        """)
        print("OK game_sessions billing columns ready")

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_venue_started
            ON game_sessions (venue_id, started_at)
        """)
        print("OK idx_sessions_venue_started index ready")

        # Invoices: one per venue per billing period (month). Line items roll up
        # per table per play-date (4am-boundary night), capped per table per night.
        # is_test venues are excluded from invoices entirely (they never pay).
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id              UUID PRIMARY KEY,
                venue_id        UUID NOT NULL REFERENCES venues(id),
                period_start    DATE NOT NULL,
                period_end      DATE NOT NULL,
                total_amount    NUMERIC NOT NULL DEFAULT 0,
                stripe_invoice_id TEXT,
                status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed')),
                created_at      TIMESTAMP DEFAULT NOW(),
                updated_at      TIMESTAMP DEFAULT NOW(),
                UNIQUE (venue_id, period_start)
            )
        """)
        print("OK invoices table ready")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_line_items (
                id            UUID PRIMARY KEY,
                invoice_id    UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                table_id      UUID NOT NULL REFERENCES tables(id),
                play_date     DATE NOT NULL,
                units_billed  INTEGER NOT NULL,
                amount        NUMERIC NOT NULL,
                cap_applied   BOOLEAN NOT NULL DEFAULT FALSE,
                created_at    TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK invoice_line_items table ready")

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_line_items_invoice
            ON invoice_line_items (invoice_id)
        """)
        print("OK idx_line_items_invoice index ready")

        # Analytics rollup: one pre-aggregated row per venue per play-date (4am
        # boundary). The nightly job recomputes recent days from game_sessions so
        # the dashboard insights/overview can read tiny summaries instead of
        # scanning raw sessions — keeps reads flat as data grows. Includes ALL
        # venues (test + real); consumers filter is_test as needed.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS venue_daily_stats (
                venue_id             UUID NOT NULL REFERENCES venues(id),
                stat_date            DATE NOT NULL,
                session_count        INTEGER NOT NULL DEFAULT 0,
                ended_count          INTEGER NOT NULL DEFAULT 0,
                total_rounds         INTEGER NOT NULL DEFAULT 0,
                sum_player_count     INTEGER NOT NULL DEFAULT 0,
                sum_duration_seconds BIGINT  NOT NULL DEFAULT 0,
                trivia_correct       INTEGER NOT NULL DEFAULT 0,
                trivia_wrong         INTEGER NOT NULL DEFAULT 0,
                cards_completed      INTEGER NOT NULL DEFAULT 0,
                cards_skipped        INTEGER NOT NULL DEFAULT 0,
                total_score          INTEGER NOT NULL DEFAULT 0,
                updated_at           TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (venue_id, stat_date)
            )
        """)
        print("OK venue_daily_stats table ready")

        # Theme system: weighted round-type / card-category recipes (gamespec
        # "Theme System"). weighting JSON = {"round_types": {...}, "card_categories": {...}}.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS themes (
                id                   UUID PRIMARY KEY,
                theme_key            TEXT UNIQUE NOT NULL,
                display_name         TEXT NOT NULL,
                weighting            JSONB NOT NULL,
                trivia_category_bias JSONB,
                is_test              BOOLEAN NOT NULL DEFAULT FALSE,
                created_at           TIMESTAMP DEFAULT NOW()
            )
        """)
        print("OK themes table ready")

        # Per venue per night: which theme is active. No selection -> default 'random'.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS nightly_theme_selections (
                id            UUID PRIMARY KEY,
                venue_id      UUID NOT NULL REFERENCES venues(id),
                selected_date DATE NOT NULL,
                theme_key     TEXT NOT NULL,
                created_at    TIMESTAMP DEFAULT NOW(),
                UNIQUE (venue_id, selected_date)
            )
        """)
        print("OK nightly_theme_selections table ready")

        from scripts.seed_themes import seed as seed_themes  # noqa: E402
        await seed_themes(conn)
        print("OK themes seeded")

        schema = await conn.fetch("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'premium_interest'
            ORDER BY ordinal_position
        """)
        print("\nSchema:")
        for col in schema:
            default = col["column_default"]
            print(f"  {col['column_name']:15} {col['data_type']:30} nullable={col['is_nullable']} default={default}")
    finally:
        await conn.close()


asyncio.run(migrate())
