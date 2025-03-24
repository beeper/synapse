# Beep beep!

import logging
from typing import TYPE_CHECKING, Optional, Tuple, cast

from synapse.storage._base import SQLBaseStore
from synapse.storage.database import (
    DatabasePool,
    LoggingDatabaseConnection,
    LoggingTransaction,
)
from synapse.types import RoomStreamToken

if TYPE_CHECKING:
    from synapse.server import HomeServer

logger = logging.getLogger(__name__)


class BeeperStore(SQLBaseStore):
    def __init__(
        self,
        database: DatabasePool,
        db_conn: LoggingDatabaseConnection,
        hs: "HomeServer",
    ):
        super().__init__(database, db_conn, hs)

        self.database = database

    async def beeper_preview_event_for_room_id_and_user_id(
        self, room_id: str, user_id: str, to_key: RoomStreamToken
    ) -> Optional[Tuple[str, int]]:
        def beeper_preview_txn(txn: LoggingTransaction) -> Optional[Tuple[str, int]]:
            sql = """
            WITH latest_event AS (
                SELECT e.event_id, e.origin_server_ts
                FROM events AS e
                LEFT JOIN redactions as r
                    ON e.event_id = r.redacts
                -- Look to see if this event itself is an edit, as we don't want to
                -- use edits ever as the "latest event"
                LEFT JOIN event_relations as is_edit
                    ON e.event_id = is_edit.event_id AND is_edit.relation_type = 'm.replace'
                WHERE
                    e.stream_ordering <= ?
                    AND e.room_id = ?
                    AND is_edit.event_id IS NULL
                    AND r.redacts IS NULL
                    AND e.type IN (
                        'm.room.message',
                        'm.room.encrypted',
                        'm.reaction',
                        'm.sticker'
                    )
                    AND CASE
                        -- Only find non-redacted reactions to our own messages
                        WHEN (e.type = 'm.reaction') THEN (
                            SELECT ? = ee.sender AND ee.event_id NOT IN (
                                SELECT redacts FROM redactions WHERE redacts = ee.event_id
                            ) FROM events as ee
                            WHERE ee.event_id = (
                                SELECT eer.relates_to_id FROM event_relations AS eer
                                WHERE eer.event_id = e.event_id
                            )
                        )
                        ELSE (true) END
                ORDER BY e.stream_ordering DESC
                LIMIT 1
            ),
            latest_edit_for_latest_event AS (
                SELECT e.event_id, e_replacement.event_id as replacement_event_id
                FROM latest_event e
                -- Find any events that edit this event, as we'll want to use the new content from
                -- the edit as the preview
                LEFT JOIN event_relations as er
                    ON e.event_id = er.relates_to_id AND er.relation_type = 'm.replace'
                LEFT JOIN events as e_replacement
                    ON er.event_id = e_replacement.event_id
                ORDER BY e_replacement.origin_server_ts DESC
                LIMIT 1
            )
            SELECT COALESCE(lefle.replacement_event_id, le.event_id), le.origin_server_ts
            FROM latest_event le
            LEFT JOIN latest_edit_for_latest_event lefle ON le.event_id = lefle.event_id
            """

            txn.execute(
                sql,
                (
                    to_key.stream,
                    room_id,
                    user_id,
                ),
            )

            return cast(Optional[Tuple[str, int]], txn.fetchone())

        return await self.db_pool.runInteraction(
            "beeper_preview_for_room_id_and_user_id",
            beeper_preview_txn,
        )
