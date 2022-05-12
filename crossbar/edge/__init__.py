##############################################################################
#
#                        Crossbar.io
#     Copyright (C) Crossbar.io Technologies GmbH. All rights reserved.
#
##############################################################################

import txaio

txaio.use_twisted()  # noqa

try:
    from autobahn import xbr
except ImportError:
    xbr = None
from crossbar._version import __version__, __build__
from crossbar.edge.personality import Personality

__all__ = ('__version__', '__build__', 'Personality', 'xbr')
__doc__ = Personality.DESC
