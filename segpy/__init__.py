import logging

__version__ = '2.0.0a5'

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())



from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
