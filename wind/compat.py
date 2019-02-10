"""

    wind.compat
    ~~~~~~~~~~~

    Python 2.x and 3.x compatibility.

"""
# flake8: noqa

import sys

ver = sys.version_info

is_py2 = (ver[0] == 2)
is_py3 = (ver[0] == 3)

if is_py2:
    unicode = unicode
    basestring = basestring
    from urlparse import urlparse, parse_qsl
    file_type = file


elif is_py3:
    unicode = str
    basestring = (str, bytes)
    from urllib.parse import urlparse, parse_qsl

    import io
    file_type = io.IOBase
