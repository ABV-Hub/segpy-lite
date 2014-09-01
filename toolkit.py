from array import array
from collections import namedtuple
import itertools
import os
import struct

from catalog import CatalogBuilder
from datatypes import CTYPES, size_in_bytes
from reel_header_definition import HEADER_DEF
from ibm_float import ibm2ieee
from revisions import canonicalize_revision
from trace_header_definition import TRACE_HEADER_DEF


REEL_HEADER_NUM_BYTES = 3600
TRACE_HEADER_NUM_BYTES = 240


def extract_revision(reel_header):
    """Obtain the SEG Y revision from the reel header.

    Args:
        reel_header: A dictionary containing a reel header, such as obtained
            from read_reel_header()

    Returns:
        One of the constants revisions.SEGY_REVISION_0 or
        revisions.SEGY_REVISION_1
    """
    raw_revision = reel_header['SegyFormatRevisionNumber']
    return canonicalize_revision(raw_revision)


def bytes_per_sample(reel_header, revision):
    """Determine the number of bytes per sample from the reel header.

    Args:
        reel_header: A dictionary containing a reel header, such as obtained
            from read_reel_header()

        revision: One of the constants revisions.SEGY_REVISION_0 or
            revisions.SEGY_REVISION_1

    Returns:
        An integer number of bytes per sample.
    """
    dsf = reel_header['DataSampleFormat']
    bps = HEADER_DEF["DataSampleFormat"]["bps"][revision][dsf]
    return bps


def samples_per_trace(reel_header):
    """Determine the number of samples per trace from the reel header.

    Note: There is no requirement for all traces to be of the same length,
        so this value should be considered indicative only, and as such is
        mostly useful in the absence of other information. The actual number
        of samples for a specific trace should be retrieved from individual
        trace headers.

    Args:
        reel_header: A dictionary containing a reel header, such as obtained
            from read_reel_header()

    Returns:
        An integer number of samples per trace
    """
    return reel_header['ns']


def trace_length_bytes(reel_header, bps):
    """Determine the trace length in bytes from the reel header.

    Note: There is no requirement for all traces to be of the same length,
        so this value should be considered indicative only, and as such is
        mostly useful in the absence of other information. The actual number
        of samples for a specific trace should be retrieved from individual
        trace headers.

    Args:
        reel_header:  A dictionary containing a reel header, such as obtained
            from read_reel_header()

        bps: The number of bytes per sample, such as obtained from a call to
            bytes_per_sample()

    """
    return samples_per_trace(reel_header) * bps + TRACE_HEADER_NUM_BYTES


def read_reel_header(fh, endian='>'):
    """Read the SEG Y reel header, also known as the binary header.

    Args:
        fh: A file-like-object open in binary mode positioned such that the
            beginning of the reel header will be the next byte to be read.

        endian: '>' for big-endian data (the standard and default), '<' for
            little-endian (non-standard)
    """
    reel_header = {}
    for key in HEADER_DEF:
        pos = HEADER_DEF[key]['pos']
        ctype = HEADER_DEF[key]['type']
        values = tuple(read_binary_values(fh, pos, ctype, 1, endian))
        reel_header[key] = values[0]
    return reel_header


def catalog_traces(fh, bps, endian='>'):
    """Determine the file offsets of each trace in the SEG Y file.

     Args:
        fh: A file-like-object open in binary mode.

        bps: The number of bytes per sample, such as obtained by a call to
            bytes_per_sample()

        endian: '>' for big-endian data (the standard and default), '<' for
            little-endian (non-standard)

    Returns:
        An immutable sequence containing byte offsets to the beginning of each trace.
    """
    trace_header_format = compile_trace_header_format(endian)

    pos_begin = REEL_HEADER_NUM_BYTES

    trace_catalog_builder = CatalogBuilder()
    line_catalog = CatalogBuilder()
    cdp_catalog = CatalogBuilder()

    for trace_number in itertools.count():
        fh.seek(pos_begin)
        data = fh.read(TRACE_HEADER_NUM_BYTES)
        if len(data) < TRACE_HEADER_NUM_BYTES:
            break
        trace_header = TraceHeader._make(trace_header_format.unpack(data))
        num_samples = trace_header.ns
        samples_bytes = num_samples * bps
        trace_catalog_builder.add(trace_number, pos_begin)
        # Should we check the data actually exists?
        line_catalog.add((trace_header.Inline3D, trace_header.Crossline3D), trace_number)
        cdp_catalog.add(trace_header.cdp, trace_number)
        pos_end = pos_begin + TRACE_HEADER_NUM_BYTES + samples_bytes
        pos_begin = pos_end

    return (trace_catalog_builder.create(),
            cdp_catalog.create(),
            line_catalog.create())


def read_binary_values(fh, pos, ctype='l', count=1, endian='>'):
    """Read a series of values from a binary file.

    Args:
        fh: A file-like-object open in binary mode.

        pos: The file offset in bytes from the beginning from which the data
            is to be read.

        ctype: The SEG Y data type.

        number: The number of items to be read.
    Returns:
        A sequence containing count items.
    """
    fmt = CTYPES[ctype]
    item_size = size_in_bytes(fmt)
    block_size = item_size * count

    fh.seek(pos, os.SEEK_SET)
    buf = fh.read(block_size)

    if len(buf) < block_size:
        raise EOFError("{} bytes requested but only {} available".format(block_size, len(buf)))

    values = unpack_ibm_floats(buf, count) if fmt == 'ibm' else unpack_values(buf, count, item_size, fmt)
    assert len(values) == count
    return values


def unpack_ibm_floats(data, count):
    """Unpack a series of binary-encoded big-endian single-precision IBM floats.

    Args:
        data: A sequence of bytes. (Python 2 - a str object, Python 3 - a bytes object)

        count: The number of floats to be read.

    Returns:
        A sequence of floats.
    """
    return array('f', (ibm2ieee(data[i: i+4]) for i in range(0, count * 4, 4)))


def unpack_values(buf, count, item_size, fmt, endian='>'):
    """Unpack a series items from a byte string.

    Args:
        data: A sequence of bytes. (Python 2 - a str object, Python 3 - a bytes object)

        count: The number of floats to be read.

        fmt: A format code (one of the values in the datatype.CTYPES dictionary)

    Returns:
        A sequence of objects with type corresponding to the format code.
    """
    c_format = '{}{}{}'.format(endian, count, fmt)
    return struct.unpack(c_format, buf)
    # We could use array.fromfile() here. On the one hand it's likely to be faster and more compact,
    # On the other, it only works on "real" files, not arbitrary file-like-objects and it would require us
    # to handle endian byte swapping ourselves.


_TraceAttributeSpec = namedtuple('Record', ['name', 'pos', 'type'])


def compile_trace_header_format(endian='>'):
    """Compile a format string for use with the struct module from the trace header definition.

    Args:
        endian: '>' for big-endian data (the standard and default), '<' for
            little-endian (non-standard)

    Returns:
        A string which can be used with the struct module for parsing trace headers.
    """

    record_specs = sorted([_TraceAttributeSpec(name, TRACE_HEADER_DEF[name]['pos'], TRACE_HEADER_DEF[name]['type']) for name in TRACE_HEADER_DEF],
                          key=lambda r : r.pos)

    fmt = [endian]
    length = 0
    for record_spec in record_specs:

        shortfall = length - record_spec.pos
        if shortfall:
            fmt.append(str(shortfall) + 'x')  # Ignore bytes
            length += shortfall

        ctype = CTYPES[record_spec.type]
        fmt.append(ctype)
        length += size_in_bytes(ctype)

    assert length == TRACE_HEADER_NUM_BYTES

    return struct.Struct(''.join(fmt))


def _compile_trace_header_record():
    """Build a TraceHeader namedtuple from the trace header definition"""
    record_specs = sorted([_TraceAttributeSpec(name, TRACE_HEADER_DEF[name]['pos'], TRACE_HEADER_DEF[name]['type']) for name in TRACE_HEADER_DEF],
                          key=lambda r : r.pos)
    return namedtuple('TraceHeader', (record_spec.name for record_spec in record_specs))


TraceHeader = _compile_trace_header_record()


