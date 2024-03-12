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
