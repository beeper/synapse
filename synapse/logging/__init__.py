# Copyright 2020 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

from synapse.logging._remote import RemoteHandler
from synapse.logging._terse_json import (
    BeeperTerseJsonFormatter,
    JsonFormatter,
    TerseJsonFormatter,
)

# These are imported to allow for nicer logging configuration files.
__all__ = [
    "RemoteHandler",
    "JsonFormatter",
    "TerseJsonFormatter",
    "BeeperTerseJsonFormatter",
]

# Debug logger for https://github.com/matrix-org/synapse/issues/9533 etc
issue9533_logger = logging.getLogger("synapse.9533_debug")
