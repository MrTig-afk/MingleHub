import asyncio

from api.db import get_pool

TABLE_ID = "5c252cf5-7a04-5465-865b-51c8cee4d83d"  # lions-den table 1


async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:
        ended = await conn.fetch(
            "UPDATE game_sessions SET ended_at = NOW(), end_reason = 'dev_reset' "
            "WHERE table_id = $1 AND ended_at IS NULL RETURNING id",
            TABLE_ID,
        )
        await conn.execute(
            "UPDATE table_lobbies SET status = 'expired' WHERE table_id = $1 AND status = 'open'",
            TABLE_ID,
        )
        print("sessions_ended:", len(ended))
    await pool.close()


asyncio.run(main())
