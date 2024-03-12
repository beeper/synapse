#
# This file is licensed under the Affero General Public License (AGPL) version 3.
#
# Copyright (C) 2023 New Vector, Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# See the GNU Affero General Public License for more details:
# <https://www.gnu.org/licenses/agpl-3.0.html>.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
#
# [This file includes modifications made by New Vector Limited]
#
#
import logging
from typing import Any, Iterable, List, Optional, Tuple

from canonicaljson import encode_canonical_json
from parameterized import parameterized

from twisted.test.proto_helpers import MemoryReactor

from synapse.api.constants import ReceiptTypes
from synapse.api.room_versions import RoomVersions
from synapse.events import EventBase, make_event_from_dict
from synapse.events.snapshot import EventContext
from synapse.handlers.room import RoomEventSource
from synapse.server import HomeServer
from synapse.storage.databases.main.event_push_actions import (
    NotifCounts,
    RoomNotifCounts,
)
from synapse.storage.databases.main.events_worker import EventsWorkerStore
from synapse.storage.roommember import GetRoomsForUserWithStreamOrdering, RoomsForUser
from synapse.types import PersistedEventPosition
from synapse.util import Clock

from tests.server import FakeTransport

from ._base import BaseWorkerStoreTestCase

USER_ID = "@feeling:test"
USER_ID_2 = "@bright:test"
OUTLIER = {"outlier": True}
ROOM_ID = "!room:test"

logger = logging.getLogger(__name__)


class EventsWorkerStoreTestCase(BaseWorkerStoreTestCase):
    STORE_TYPE = EventsWorkerStore

    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        super().prepare(reactor, clock, hs)

        self.get_success(
            self.master_store.store_room(
                ROOM_ID,
                USER_ID,
                is_public=False,
                room_version=RoomVersions.V1,
            )
        )

    def assertEventsEqual(
        self, first: EventBase, second: EventBase, msg: Optional[Any] = None
    ) -> None:
        self.assertEqual(
            encode_canonical_json(first.get_pdu_json()),
            encode_canonical_json(second.get_pdu_json()),
            msg,
        )

    def test_get_latest_event_ids_in_room(self) -> None:
        create = self.persist(type="m.room.create", key="", creator=USER_ID)
        self.replicate()
        self.check("get_latest_event_ids_in_room", (ROOM_ID,), {create.event_id})

        join = self.persist(
            type="m.room.member",
            key=USER_ID,
            membership="join",
            prev_events=[(create.event_id, {})],
        )
        self.replicate()
        self.check("get_latest_event_ids_in_room", (ROOM_ID,), {join.event_id})

    def test_redactions(self) -> None:
        self.persist(type="m.room.create", key="", creator=USER_ID)
        self.persist(type="m.room.member", key=USER_ID, membership="join")

        msg = self.persist(type="m.room.message", msgtype="m.text", body="Hello")
        self.replicate()
        self.check("get_event", [msg.event_id], msg, asserter=self.assertEventsEqual)

        redaction = self.persist(type="m.room.redaction", redacts=msg.event_id)
        self.replicate()

        msg_dict = msg.get_dict()
        msg_dict["content"] = {}
        msg_dict["unsigned"]["redacted_by"] = redaction.event_id
        msg_dict["unsigned"]["redacted_because"] = redaction
        redacted = make_event_from_dict(
            msg_dict, internal_metadata_dict=msg.internal_metadata.get_dict()
        )
        self.check(
            "get_event", [msg.event_id], redacted, asserter=self.assertEventsEqual
        )

    def test_backfilled_redactions(self) -> None:
        self.persist(type="m.room.create", key="", creator=USER_ID)
        self.persist(type="m.room.member", key=USER_ID, membership="join")

        msg = self.persist(type="m.room.message", msgtype="m.text", body="Hello")
        self.replicate()
        self.check("get_event", [msg.event_id], msg, asserter=self.assertEventsEqual)

        redaction = self.persist(
            type="m.room.redaction", redacts=msg.event_id, backfill=True
        )
        self.replicate()

        msg_dict = msg.get_dict()
        msg_dict["content"] = {}
        msg_dict["unsigned"]["redacted_by"] = redaction.event_id
        msg_dict["unsigned"]["redacted_because"] = redaction
        redacted = make_event_from_dict(
            msg_dict, internal_metadata_dict=msg.internal_metadata.get_dict()
        )
        self.check(
            "get_event", [msg.event_id], redacted, asserter=self.assertEventsEqual
        )

    def test_invites(self) -> None:
        self.persist(type="m.room.create", key="", creator=USER_ID)
        self.check("get_invited_rooms_for_local_user", [USER_ID_2], [])
        event = self.persist(type="m.room.member", key=USER_ID_2, membership="invite")
        assert event.internal_metadata.stream_ordering is not None

        self.replicate()

        self.check(
            "get_invited_rooms_for_local_user",
            [USER_ID_2],
            [
                RoomsForUser(
                    ROOM_ID,
                    USER_ID,
                    "invite",
                    event.event_id,
                    event.internal_metadata.stream_ordering,
                    RoomVersions.V1.identifier,
                )
            ],
        )

    @parameterized.expand([(True,), (False,)])
    def test_push_actions_for_user(self, send_receipt: bool) -> None:
        self.persist(type="m.room.create", key="", creator=USER_ID)
        self.persist(type="m.room.member", key=USER_ID, membership="join")
        self.persist(
            type="m.room.member", sender=USER_ID, key=USER_ID_2, membership="join"
        )
        event1 = self.persist(type="m.room.message", msgtype="m.text", body="hello")
        self.replicate()

        if send_receipt:
            self.get_success(
                self.master_store.insert_receipt(
                    ROOM_ID, ReceiptTypes.READ, USER_ID_2, [event1.event_id], None, {}
                )
            )

        self.check(
            "get_unread_event_push_actions_by_room_for_user",
            [ROOM_ID, USER_ID_2],
            RoomNotifCounts(
                NotifCounts(highlight_count=0, unread_count=0, notify_count=0), {}
            ),
        )

        self.persist(
            type="m.room.message",
            msgtype="m.text",
            body="world",
            push_actions=[(USER_ID_2, ["notify"])],
        )
        self.replicate()
        self.check(
            "get_unread_event_push_actions_by_room_for_user",
            [ROOM_ID, USER_ID_2],
            RoomNotifCounts(
                NotifCounts(highlight_count=0, unread_count=0, notify_count=1), {}
            ),
        )

        self.persist(
            type="m.room.message",
            msgtype="m.text",
            body="world",
            push_actions=[
                (USER_ID_2, ["notify", {"set_tweak": "highlight", "value": True}])
            ],
        )
        self.replicate()
        self.check(
            "get_unread_event_push_actions_by_room_for_user",
            [ROOM_ID, USER_ID_2],
            RoomNotifCounts(
                NotifCounts(highlight_count=1, unread_count=0, notify_count=2), {}
            ),
        )

    def test_get_rooms_for_user_with_stream_ordering(self) -> None:
        """Check that the cache on get_rooms_for_user_with_stream_ordering is invalidated
        by rows in the events stream
        """
        self.persist(type="m.room.create", key="", creator=USER_ID)
        self.persist(type="m.room.member", key=USER_ID, membership="join")
        self.replicate()
        self.check("get_rooms_for_user_with_stream_ordering", (USER_ID_2,), set())

        j2 = self.persist(
            type="m.room.member", sender=USER_ID_2, key=USER_ID_2, membership="join"
        )
        assert j2.internal_metadata.stream_ordering is not None
        self.replicate()

        expected_pos = PersistedEventPosition(
            "master", j2.internal_metadata.stream_ordering
        )
        self.check(
            "get_rooms_for_user_with_stream_ordering",
            (USER_ID_2,),
            {GetRoomsForUserWithStreamOrdering(ROOM_ID, expected_pos)},
        )

    def test_get_rooms_for_user_with_stream_ordering_with_multi_event_persist(
        self,
    ) -> None:
        """Check that current_state invalidation happens correctly with multiple events
        in the persistence batch.

        This test attempts to reproduce a race condition between the event persistence
        loop and a worker-based Sync handler.

        The problem occurred when the master persisted several events in one batch. It
        only updates the current_state at the end of each batch, so the obvious thing
        to do is then to issue a current_state_delta stream update corresponding to the
        last stream_id in the batch.

        However, that raises the possibility that a worker will see the replication
        notification for a join event before the current_state caches are invalidated.

        The test involves:
         * creating a join and a message event for a user, and persisting them in the
           same batch

         * controlling the replication stream so that updates are sent gradually

         * between each bunch of replication updates, check that we see a consistent
           snapshot of the state.
        """
        self.persist(type="m.room.create", key="", creator=USER_ID)
        self.persist(type="m.room.member", key=USER_ID, membership="join")
        self.replicate()
        self.check("get_rooms_for_user_with_stream_ordering", (USER_ID_2,), set())

        # limit the replication rate
        repl_transport = self._server_transport
        assert isinstance(repl_transport, FakeTransport)
        repl_transport.autoflush = False

        # build the join and message events and persist them in the same batch.
        logger.info("----- build test events ------")
        j2, j2ctx = self.build_event(
            type="m.room.member", sender=USER_ID_2, key=USER_ID_2, membership="join"
        )
        msg, msgctx = self.build_event()
        self.get_success(self.persistance.persist_events([(j2, j2ctx), (msg, msgctx)]))
        self.replicate()
        assert j2.internal_metadata.stream_ordering is not None

        event_source = RoomEventSource(self.hs)
        event_source.store = self.worker_store
        current_token = event_source.get_current_key()

        # gradually stream out the replication
        while repl_transport.buffer:
            logger.info("------ flush ------")
            repl_transport.flush(30)
            self.pump(0)

            prev_token = current_token
            current_token = event_source.get_current_key()

            # attempt to replicate the behaviour of the sync handler.
            #
            # First, we get a list of the rooms we are joined to
            joined_rooms = self.get_success(
                self.worker_store.get_rooms_for_user_with_stream_ordering(USER_ID_2)
            )

            # Then, we get a list of the events since the last sync
            membership_changes = self.get_success(
                self.worker_store.get_membership_changes_for_user(
                    USER_ID_2, prev_token, current_token
                )
            )

            logger.info(
                "%s->%s: joined_rooms=%r membership_changes=%r",
                prev_token,
                current_token,
                joined_rooms,
                membership_changes,
            )

            # the membership change is only any use to us if the room is in the
            # joined_rooms list.
            if membership_changes:
                expected_pos = PersistedEventPosition(
                    "master", j2.internal_metadata.stream_ordering
                )
                self.assertEqual(
                    joined_rooms,
                    {GetRoomsForUserWithStreamOrdering(ROOM_ID, expected_pos)},
                )

    event_id = 0

    def persist(self, backfill: bool = False, **kwargs: Any) -> EventBase:
        """
        Returns:
            The event that was persisted.
        """
        event, context = self.build_event(**kwargs)

        if backfill:
            self.get_success(
                self.persistance.persist_events([(event, context)], backfilled=True)
            )
        else:
            self.get_success(self.persistance.persist_event(event, context))

        return event

    def build_event(
        self,
        sender: str = USER_ID,
        room_id: str = ROOM_ID,
        type: str = "m.room.message",
        key: Optional[str] = None,
        internal: Optional[dict] = None,
        depth: Optional[int] = None,
        prev_events: Optional[List[Tuple[str, dict]]] = None,
        auth_events: Optional[List[str]] = None,
        prev_state: Optional[List[str]] = None,
        redacts: Optional[str] = None,
        push_actions: Iterable = frozenset(),
        **content: object,
    ) -> Tuple[EventBase, EventContext]:
        prev_events = prev_events or []
        auth_events = auth_events or []
        prev_state = prev_state or []

        if depth is None:
            depth = self.event_id

        if not prev_events:
            latest_event_ids = self.get_success(
                self.master_store.get_latest_event_ids_in_room(room_id)
            )
            prev_events = [(ev_id, {}) for ev_id in latest_event_ids]

        event_dict = {
            "sender": sender,
            "type": type,
            "content": content,
            "event_id": "$%d:blue" % (self.event_id,),
            "room_id": room_id,
            "depth": depth,
            "origin_server_ts": self.event_id,
            "prev_events": prev_events,
            "auth_events": auth_events,
        }
        if key is not None:
            event_dict["state_key"] = key
            event_dict["prev_state"] = prev_state

        if redacts is not None:
            event_dict["redacts"] = redacts

        event = make_event_from_dict(event_dict, internal_metadata_dict=internal or {})

        self.event_id += 1
        state_handler = self.hs.get_state_handler()
        context = self.get_success(state_handler.compute_event_context(event))

        self.get_success(
            self.master_store.add_push_actions_to_staging(
                event.event_id,
                dict(push_actions),
                {user_id: False for user_id, _ in push_actions},
                "main",
            )
        )
        return event, context
