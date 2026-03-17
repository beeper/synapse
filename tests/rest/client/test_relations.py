#
# This file is licensed under the Affero General Public License (AGPL) version 3.
#
# Copyright 2021 The Matrix.org Foundation C.I.C.
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

import urllib.parse
from typing import Any
from unittest.mock import AsyncMock, patch

from twisted.internet.testing import MemoryReactor

from synapse.api.constants import AccountDataTypes, EventTypes, RelationTypes
from synapse.rest import admin
from synapse.rest.client import login, register, relations, room, sync
from synapse.server import HomeServer
from synapse.util.clock import Clock

from tests import unittest
from tests.server import FakeChannel
from tests.test_utils.event_injection import inject_event


class BaseRelationsTestCase(unittest.HomeserverTestCase):
    servlets = [
        relations.register_servlets,
        room.register_servlets,
        sync.register_servlets,
        login.register_servlets,
        register.register_servlets,
        admin.register_servlets_for_client_rest_resource,
    ]
    hijack_auth = False

    def default_config(self) -> dict[str, Any]:
        # We need to enable msc1849 support for aggregations
        config = super().default_config()

        # We enable frozen dicts as relations/edits change event contents, so we
        # want to test that we don't modify the events in the caches.
        config["use_frozen_dicts"] = True

        return config

    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        self.store = hs.get_datastores().main

        self.user_id, self.user_token = self._create_user("alice")
        self.user2_id, self.user2_token = self._create_user("bob")

        self.room = self.helper.create_room_as(self.user_id, tok=self.user_token)
        self.helper.join(self.room, user=self.user2_id, tok=self.user2_token)
        res = self.helper.send(self.room, body="Hi!", tok=self.user_token)
        self.parent_id = res["event_id"]

    def _create_user(self, localpart: str) -> tuple[str, str]:
        user_id = self.register_user(localpart, "abc123")
        access_token = self.login(localpart, "abc123")

        return user_id, access_token

    def _send_relation(
        self,
        relation_type: str,
        event_type: str,
        key: str | None = None,
        content: dict | None = None,
        access_token: str | None = None,
        parent_id: str | None = None,
        expected_response_code: int = 200,
    ) -> FakeChannel:
        """Helper function to send a relation pointing at `self.parent_id`

        Args:
            relation_type: One of `RelationTypes`
            event_type: The type of the event to create
            key: The aggregation key used for m.annotation relation type.
            content: The content of the created event. Will be modified to configure
                the m.relates_to key based on the other provided parameters.
            access_token: The access token used to send the relation, defaults
                to `self.user_token`
            parent_id: The event_id this relation relates to. If None, then self.parent_id

        Returns:
            FakeChannel
        """
        if not access_token:
            access_token = self.user_token

        original_id = parent_id if parent_id else self.parent_id

        if content is None:
            content = {}
        content["m.relates_to"] = {
            "event_id": original_id,
            "rel_type": relation_type,
        }
        if key is not None:
            content["m.relates_to"]["key"] = key

        channel = self.make_request(
            "POST",
            f"/_matrix/client/v3/rooms/{self.room}/send/{event_type}",
            content,
            access_token=access_token,
        )
        self.assertEqual(expected_response_code, channel.code, channel.json_body)
        return channel

    def _get_related_events(self) -> list[str]:
        """
        Requests /relations on the parent ID and returns a list of event IDs.
        """
        # Request the relations of the event.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        return [ev["event_id"] for ev in channel.json_body["chunk"]]


class RelationsTestCase(BaseRelationsTestCase):
    def test_send_relation(self) -> None:
        """Tests that sending a relation works."""
        channel = self._send_relation(RelationTypes.ANNOTATION, "m.reaction", key="👍")
        event_id = channel.json_body["event_id"]

        channel = self.make_request(
            "GET",
            f"/rooms/{self.room}/event/{event_id}",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)

        self.assert_dict(
            {
                "type": "m.reaction",
                "sender": self.user_id,
                "content": {
                    "m.relates_to": {
                        "event_id": self.parent_id,
                        "key": "👍",
                        "rel_type": RelationTypes.ANNOTATION,
                    }
                },
            },
            channel.json_body,
        )

    def test_deny_invalid_event(self) -> None:
        """Test that we deny relations on non-existant events"""
        self._send_relation(
            RelationTypes.ANNOTATION,
            EventTypes.Message,
            parent_id="foo",
            content={"body": "foo", "msgtype": "m.text"},
            expected_response_code=400,
        )

        # Unless that event is referenced from another event!
        self.get_success(
            self.hs.get_datastores().main.db_pool.simple_insert(
                table="event_relations",
                values={
                    "event_id": "bar",
                    "relates_to_id": "foo",
                    "relation_type": RelationTypes.THREAD,
                },
                desc="test_deny_invalid_event",
            )
        )
        self._send_relation(
            RelationTypes.THREAD,
            EventTypes.Message,
            parent_id="foo",
            content={"body": "foo", "msgtype": "m.text"},
        )

    def test_deny_invalid_room(self) -> None:
        """Test that we deny relations on non-existant events"""
        # Create another room and send a message in it.
        room2 = self.helper.create_room_as(self.user_id, tok=self.user_token)
        res = self.helper.send(room2, body="Hi!", tok=self.user_token)
        parent_id = res["event_id"]

        # Attempt to send an annotation to that event.
        self._send_relation(
            RelationTypes.ANNOTATION,
            "m.reaction",
            parent_id=parent_id,
            key="A",
            expected_response_code=400,
        )

    def test_deny_double_react(self) -> None:
        """Test that we deny relations on membership events"""
        self._send_relation(RelationTypes.ANNOTATION, "m.reaction", key="a")
        self._send_relation(
            RelationTypes.ANNOTATION, "m.reaction", "a", expected_response_code=400
        )

    def test_deny_forked_thread(self) -> None:
        """It is invalid to start a thread off a thread."""
        channel = self._send_relation(
            RelationTypes.THREAD,
            "m.room.message",
            content={"msgtype": "m.text", "body": "foo"},
            parent_id=self.parent_id,
        )
        parent_id = channel.json_body["event_id"]

        self._send_relation(
            RelationTypes.THREAD,
            "m.room.message",
            content={"msgtype": "m.text", "body": "foo"},
            parent_id=parent_id,
            expected_response_code=400,
        )

    def test_ignore_invalid_room(self) -> None:
        """Test that we ignore invalid relations over federation."""
        # Create another room and send a message in it.
        room2 = self.helper.create_room_as(self.user_id, tok=self.user_token)
        res = self.helper.send(room2, body="Hi!", tok=self.user_token)
        parent_id = res["event_id"]

        # Disable the validation to pretend this came over federation.
        with patch(
            "synapse.handlers.message.EventCreationHandler._validate_event_relation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            # Generate a various relations from a different room.
            self.get_success(
                inject_event(
                    self.hs,
                    room_id=self.room,
                    type="m.reaction",
                    sender=self.user_id,
                    content={
                        "m.relates_to": {
                            "rel_type": RelationTypes.ANNOTATION,
                            "event_id": parent_id,
                            "key": "A",
                        }
                    },
                )
            )

            self.get_success(
                inject_event(
                    self.hs,
                    room_id=self.room,
                    type="m.room.message",
                    sender=self.user_id,
                    content={
                        "body": "foo",
                        "msgtype": "m.text",
                        "m.relates_to": {
                            "rel_type": RelationTypes.REFERENCE,
                            "event_id": parent_id,
                        },
                    },
                )
            )

            self.get_success(
                inject_event(
                    self.hs,
                    room_id=self.room,
                    type="m.room.message",
                    sender=self.user_id,
                    content={
                        "body": "foo",
                        "msgtype": "m.text",
                        "m.relates_to": {
                            "rel_type": RelationTypes.THREAD,
                            "event_id": parent_id,
                        },
                    },
                )
            )

            self.get_success(
                inject_event(
                    self.hs,
                    room_id=self.room,
                    type="m.room.message",
                    sender=self.user_id,
                    content={
                        "body": "foo",
                        "msgtype": "m.text",
                        "new_content": {
                            "body": "new content",
                            "msgtype": "m.text",
                        },
                        "m.relates_to": {
                            "rel_type": RelationTypes.REPLACE,
                            "event_id": parent_id,
                        },
                    },
                )
            )

        # They should be ignored when fetching relations.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{room2}/relations/{parent_id}",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        self.assertEqual(channel.json_body["chunk"], [])

        # And for bundled aggregations.
        channel = self.make_request(
            "GET",
            f"/rooms/{room2}/event/{parent_id}",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        self.assertNotIn("m.relations", channel.json_body["unsigned"])

    def test_unknown_relations(self) -> None:
        """Unknown relations should be accepted."""
        channel = self._send_relation("m.relation.test", "m.room.test")
        event_id = channel.json_body["event_id"]

        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}?limit=1",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)

        # We expect to get back a single pagination result, which is the full
        # relation event we sent above.
        self.assertEqual(len(channel.json_body["chunk"]), 1, channel.json_body)
        self.assert_dict(
            {"event_id": event_id, "sender": self.user_id, "type": "m.room.test"},
            channel.json_body["chunk"][0],
        )

        # We also expect to get the original event (the id of which is self.parent_id)
        # when requesting the unstable endpoint.
        self.assertNotIn("original_event", channel.json_body)
        channel = self.make_request(
            "GET",
            f"/_matrix/client/unstable/rooms/{self.room}/relations/{self.parent_id}?limit=1",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        self.assertEqual(
            channel.json_body["original_event"]["event_id"], self.parent_id
        )

        # When bundling the unknown relation is not included.
        channel = self.make_request(
            "GET",
            f"/rooms/{self.room}/event/{self.parent_id}",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        self.assertNotIn("m.relations", channel.json_body["unsigned"])

    def test_background_update(self) -> None:
        """Test the event_arbitrary_relations background update."""
        channel = self._send_relation(RelationTypes.ANNOTATION, "m.reaction", key="👍")
        annotation_event_id_good = channel.json_body["event_id"]

        channel = self._send_relation(RelationTypes.ANNOTATION, "m.reaction", key="A")
        annotation_event_id_bad = channel.json_body["event_id"]

        channel = self._send_relation(RelationTypes.THREAD, "m.room.test")
        thread_event_id = channel.json_body["event_id"]

        # Clean-up the table as if the inserts did not happen during event creation.
        self.get_success(
            self.store.db_pool.simple_delete_many(
                table="event_relations",
                column="event_id",
                iterable=(annotation_event_id_bad, thread_event_id),
                keyvalues={},
                desc="RelationsTestCase.test_background_update",
            )
        )

        # Only the "good" annotation should be found.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}?limit=10",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        self.assertEqual(
            [ev["event_id"] for ev in channel.json_body["chunk"]],
            [annotation_event_id_good],
        )

        # Insert and run the background update.
        self.get_success(
            self.store.db_pool.simple_insert(
                "background_updates",
                {"update_name": "event_arbitrary_relations", "progress_json": "{}"},
            )
        )

        # Ugh, have to reset this flag
        self.store.db_pool.updates._all_done = False
        self.wait_for_background_updates()

        # The "good" annotation and the thread should be found, but not the "bad"
        # annotation.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}?limit=10",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        self.assertCountEqual(
            [ev["event_id"] for ev in channel.json_body["chunk"]],
            [annotation_event_id_good, thread_event_id],
        )


class RelationPaginationTestCase(BaseRelationsTestCase):
    def test_basic_paginate_relations(self) -> None:
        """Tests that calling pagination API correctly the latest relations."""
        channel = self._send_relation(RelationTypes.ANNOTATION, "m.reaction", "a")
        first_annotation_id = channel.json_body["event_id"]

        channel = self._send_relation(RelationTypes.ANNOTATION, "m.reaction", "b")
        second_annotation_id = channel.json_body["event_id"]

        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}?limit=1",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)

        # We expect to get back a single pagination result, which is the latest
        # full relation event we sent above.
        self.assertEqual(len(channel.json_body["chunk"]), 1, channel.json_body)
        self.assert_dict(
            {
                "event_id": second_annotation_id,
                "sender": self.user_id,
                "type": "m.reaction",
            },
            channel.json_body["chunk"][0],
        )

        # Make sure next_batch has something in it that looks like it could be a
        # valid token.
        self.assertIsInstance(
            channel.json_body.get("next_batch"), str, channel.json_body
        )

        # Request the relations again, but with a different direction.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations"
            f"/{self.parent_id}?limit=1&dir=f",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)

        # We expect to get back a single pagination result, which is the earliest
        # full relation event we sent above.
        self.assertEqual(len(channel.json_body["chunk"]), 1, channel.json_body)
        self.assert_dict(
            {
                "event_id": first_annotation_id,
                "sender": self.user_id,
                "type": "m.reaction",
            },
            channel.json_body["chunk"][0],
        )

    def test_repeated_paginate_relations(self) -> None:
        """Test that if we paginate using a limit and tokens then we get the
        expected events.
        """

        expected_event_ids = []
        for idx in range(10):
            channel = self._send_relation(
                RelationTypes.ANNOTATION, "m.reaction", chr(ord("a") + idx)
            )
            expected_event_ids.append(channel.json_body["event_id"])

        prev_token: str | None = ""
        found_event_ids: list[str] = []
        for _ in range(20):
            from_token = ""
            if prev_token:
                from_token = "&from=" + prev_token

            channel = self.make_request(
                "GET",
                f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}?limit=3{from_token}",
                access_token=self.user_token,
            )
            self.assertEqual(200, channel.code, channel.json_body)

            found_event_ids.extend(e["event_id"] for e in channel.json_body["chunk"])
            next_batch = channel.json_body.get("next_batch")

            self.assertNotEqual(prev_token, next_batch)
            prev_token = next_batch

            if not prev_token:
                break

        # We paginated backwards, so reverse
        found_event_ids.reverse()
        self.assertEqual(found_event_ids, expected_event_ids)

        # Test forward pagination.
        prev_token = ""
        found_event_ids = []
        for _ in range(20):
            from_token = ""
            if prev_token:
                from_token = "&from=" + prev_token

            channel = self.make_request(
                "GET",
                f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}?dir=f&limit=3{from_token}",
                access_token=self.user_token,
            )
            self.assertEqual(200, channel.code, channel.json_body)

            found_event_ids.extend(e["event_id"] for e in channel.json_body["chunk"])
            next_batch = channel.json_body.get("next_batch")

            self.assertNotEqual(prev_token, next_batch)
            prev_token = next_batch

            if not prev_token:
                break

        self.assertEqual(found_event_ids, expected_event_ids)

    def test_pagination_from_sync_and_messages(self) -> None:
        """Pagination tokens from /sync and /messages can be used to paginate /relations."""
        channel = self._send_relation(RelationTypes.ANNOTATION, "m.reaction", "A")
        annotation_id = channel.json_body["event_id"]
        # Send an event after the relation events.
        self.helper.send(self.room, body="Latest event", tok=self.user_token)

        # Request /sync, limiting it such that only the latest event is returned
        # (and not the relation).
        filter = urllib.parse.quote_plus(b'{"room": {"timeline": {"limit": 1}}}')
        channel = self.make_request(
            "GET", f"/sync?filter={filter}", access_token=self.user_token
        )
        self.assertEqual(200, channel.code, channel.json_body)
        room_timeline = channel.json_body["rooms"]["join"][self.room]["timeline"]
        sync_prev_batch = room_timeline["prev_batch"]
        self.assertIsNotNone(sync_prev_batch)
        # Ensure the relation event is not in the batch returned from /sync.
        self.assertNotIn(
            annotation_id, [ev["event_id"] for ev in room_timeline["events"]]
        )

        # Request /messages, limiting it such that only the latest event is
        # returned (and not the relation).
        channel = self.make_request(
            "GET",
            f"/rooms/{self.room}/messages?dir=b&limit=1",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        messages_end = channel.json_body["end"]
        self.assertIsNotNone(messages_end)
        # Ensure the relation event is not in the chunk returned from /messages.
        self.assertNotIn(
            annotation_id, [ev["event_id"] for ev in channel.json_body["chunk"]]
        )

        # Request /relations with the pagination tokens received from both the
        # /sync and /messages responses above, in turn.
        #
        # This is a tiny bit silly since the client wouldn't know the parent ID
        # from the requests above; consider the parent ID to be known from a
        # previous /sync.
        for from_token in (sync_prev_batch, messages_end):
            channel = self.make_request(
                "GET",
                f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}?from={from_token}",
                access_token=self.user_token,
            )
            self.assertEqual(200, channel.code, channel.json_body)

            # The relation should be in the returned chunk.
            self.assertIn(
                annotation_id, [ev["event_id"] for ev in channel.json_body["chunk"]]
            )


class RecursiveRelationTestCase(BaseRelationsTestCase):
    def test_recursive_relations(self) -> None:
        """Generate a complex, multi-level relationship tree and query it."""
        # Create a thread with a few messages in it.
        channel = self._send_relation(RelationTypes.THREAD, "m.room.test")
        thread_1 = channel.json_body["event_id"]

        channel = self._send_relation(RelationTypes.THREAD, "m.room.test")
        thread_2 = channel.json_body["event_id"]

        # Add annotations.
        channel = self._send_relation(
            RelationTypes.ANNOTATION, "m.reaction", "a", parent_id=thread_2
        )
        annotation_1 = channel.json_body["event_id"]

        channel = self._send_relation(
            RelationTypes.ANNOTATION, "m.reaction", "b", parent_id=thread_1
        )
        annotation_2 = channel.json_body["event_id"]

        # Add a reference to part of the thread, then edit the reference and annotate it.
        channel = self._send_relation(
            RelationTypes.REFERENCE, "m.room.test", parent_id=thread_2
        )
        reference_1 = channel.json_body["event_id"]

        channel = self._send_relation(
            RelationTypes.ANNOTATION, "m.reaction", "c", parent_id=reference_1
        )
        annotation_3 = channel.json_body["event_id"]

        channel = self._send_relation(
            RelationTypes.REPLACE,
            "m.room.test",
            parent_id=reference_1,
        )
        edit = channel.json_body["event_id"]

        # Also more events off the root.
        channel = self._send_relation(RelationTypes.ANNOTATION, "m.reaction", "d")
        annotation_4 = channel.json_body["event_id"]

        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}"
            "?dir=f&limit=20&recurse=true",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)

        # The above events should be returned in creation order.
        event_ids = [ev["event_id"] for ev in channel.json_body["chunk"]]
        self.assertEqual(
            event_ids,
            [
                thread_1,
                thread_2,
                annotation_1,
                annotation_2,
                reference_1,
                annotation_3,
                edit,
                annotation_4,
            ],
        )

    def test_recursive_relations_with_filter(self) -> None:
        """The event_type and rel_type still apply."""
        # Create a thread with a few messages in it.
        channel = self._send_relation(RelationTypes.THREAD, "m.room.test")
        thread_1 = channel.json_body["event_id"]

        # Add annotations.
        channel = self._send_relation(
            RelationTypes.ANNOTATION, "m.reaction", "b", parent_id=thread_1
        )
        annotation_1 = channel.json_body["event_id"]

        # Add a reference to part of the thread, then edit the reference and annotate it.
        channel = self._send_relation(
            RelationTypes.REFERENCE, "m.room.test", parent_id=thread_1
        )
        reference_1 = channel.json_body["event_id"]

        channel = self._send_relation(
            RelationTypes.ANNOTATION, "org.matrix.reaction", "c", parent_id=reference_1
        )
        annotation_2 = channel.json_body["event_id"]

        # Fetch only annotations, but recursively.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}/{RelationTypes.ANNOTATION}"
            "?dir=f&limit=20&recurse=true",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)

        # The above events should be returned in creation order.
        event_ids = [ev["event_id"] for ev in channel.json_body["chunk"]]
        self.assertEqual(event_ids, [annotation_1, annotation_2])

        # Fetch only m.reactions, but recursively.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/relations/{self.parent_id}/{RelationTypes.ANNOTATION}/m.reaction"
            "?dir=f&limit=20&recurse=true",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)

        # The above events should be returned in creation order.
        event_ids = [ev["event_id"] for ev in channel.json_body["chunk"]]
        self.assertEqual(event_ids, [annotation_1])


class ThreadsTestCase(BaseRelationsTestCase):
    def test_pagination(self) -> None:
        """Create threads and paginate through them."""
        # Create 2 threads.
        thread_1 = self.parent_id
        res = self.helper.send(self.room, body="Thread Root!", tok=self.user_token)
        thread_2 = res["event_id"]

        self._send_relation(RelationTypes.THREAD, "m.room.test")
        self._send_relation(RelationTypes.THREAD, "m.room.test", parent_id=thread_2)

        # Request the threads in the room.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/threads?limit=1",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        thread_roots = [ev["event_id"] for ev in channel.json_body["chunk"]]
        self.assertEqual(thread_roots, [thread_2])

        # Make sure next_batch has something in it that looks like it could be a
        # valid token.
        next_batch = channel.json_body.get("next_batch")
        self.assertIsInstance(next_batch, str, channel.json_body)

        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/threads?limit=1&from={next_batch}",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        thread_roots = [ev["event_id"] for ev in channel.json_body["chunk"]]
        self.assertEqual(thread_roots, [thread_1], channel.json_body)

        self.assertNotIn("next_batch", channel.json_body, channel.json_body)

    def test_include(self) -> None:
        """Filtering threads to all or participated in should work."""
        # Thread 1 has the user as the root event.
        thread_1 = self.parent_id
        self._send_relation(
            RelationTypes.THREAD, "m.room.test", access_token=self.user2_token
        )

        # Thread 2 has the user replying.
        res = self.helper.send(self.room, body="Thread Root!", tok=self.user2_token)
        thread_2 = res["event_id"]
        self._send_relation(RelationTypes.THREAD, "m.room.test", parent_id=thread_2)

        # Thread 3 has the user not participating in.
        res = self.helper.send(self.room, body="Another thread!", tok=self.user2_token)
        thread_3 = res["event_id"]
        self._send_relation(
            RelationTypes.THREAD,
            "m.room.test",
            access_token=self.user2_token,
            parent_id=thread_3,
        )

        # All threads in the room.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/threads",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        thread_roots = [ev["event_id"] for ev in channel.json_body["chunk"]]
        self.assertEqual(
            thread_roots, [thread_3, thread_2, thread_1], channel.json_body
        )

        # Only participated threads.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/threads?include=participated",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        thread_roots = [ev["event_id"] for ev in channel.json_body["chunk"]]
        self.assertEqual(thread_roots, [thread_2, thread_1], channel.json_body)

    def test_ignored_user(self) -> None:
        """Events from ignored users should be ignored."""
        # Thread 1 has a reply from an ignored user.
        thread_1 = self.parent_id
        self._send_relation(
            RelationTypes.THREAD, "m.room.test", access_token=self.user2_token
        )

        # Thread 2 is created by an ignored user.
        res = self.helper.send(self.room, body="Thread Root!", tok=self.user2_token)
        thread_2 = res["event_id"]
        self._send_relation(RelationTypes.THREAD, "m.room.test", parent_id=thread_2)

        # Ignore user2.
        self.get_success(
            self.store.add_account_data_for_user(
                self.user_id,
                AccountDataTypes.IGNORED_USER_LIST,
                {"ignored_users": {self.user2_id: {}}},
            )
        )

        # Only thread 1 is returned.
        channel = self.make_request(
            "GET",
            f"/_matrix/client/v1/rooms/{self.room}/threads",
            access_token=self.user_token,
        )
        self.assertEqual(200, channel.code, channel.json_body)
        thread_roots = [ev["event_id"] for ev in channel.json_body["chunk"]]
        self.assertEqual(thread_roots, [thread_1], channel.json_body)
