###############################################################################
#
# Crossbar.io Master
# Copyright (c) Crossbar.io Technologies GmbH. Licensed under EUPLv1.2.
#
###############################################################################

try:
    from autobahn import xbr
except ImportError:
    xbr = None
from crossbar._version import __version__, __build__

import txaio

txaio.use_twisted()

from crossbar.master.personality import Personality  # noqa

__all__ = ('__version__', '__build__', 'Personality', 'xbr')
__doc__ = Personality.DESC
