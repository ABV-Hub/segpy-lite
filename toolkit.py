from array import array
from collections import namedtuple
import itertools
import os
import struct

from catalog import CatalogBuilder
from datatypes import CTYPES, size_in_bytes
from reel_header_definition import HEADER_DEF
from ibm_float import ibm2ieee, ieee2ibm
from revisions import canonicalize_revision
from trace_header_definition import TRACE_HEADER_DEF
from util import file_length
from portability import EMPTY_BYTE_STRING


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


_READ_PROPORTION = 0.75  # The proportion of time spent in catalog_traces
                         # reading the file. Determined empirically.


def catalog_traces(fh, bps, endian='>', progress=None):
    """Build catalogs to facilitate random access to trace data.

    Note:
        This function can take significant time to run, proportional
        to the number of traces in the SEG Y file.

    Four catalogs will be build:

     1. A catalog mapping trace index (0-based) to the position of that
        trace header in the file.

     2. A catalog mapping trace index (0-based) to the number of
        samples in that trace.

     3. A catalog mapping CDP number to the trace index.

     4. A catalog mapping an (inline, crossline) number 2-tuple to
        trace index.

    Args:
        fh: A file-like-object open in binary mode.

        bps: The number of bytes per sample, such as obtained by a call
            to bytes_per_sample()

        endian: '>' for big-endian data (the standard and default), '<'
            for little-endian (non-standard)

        progress: A unary callable which will be passed a number
            between zero and one indicating the progress made. If
            provided, this callback will be invoked at least once with
            an argument equal to 1

    Returns:
        A 4-tuple of the form (trace-offset-catalog,
                               trace-length-catalog,
                               cdp-catalog,
                               line-catalog)` where
        each catalog is an instance of ``collections.Mapping`` or None
        if no catalog could be built.
    """
    progress_callback = progress if progress is not None else lambda p: None

    if not callable(progress_callback):
        raise TypeError("catalog_traces(): progress callback must be callable")

    trace_header_format = compile_trace_header_format(endian)

    length = file_length(fh)

    pos_begin = REEL_HEADER_NUM_BYTES

    trace_offset_catalog_builder = CatalogBuilder()
    trace_length_catalog_builder = CatalogBuilder()
    line_catalog_builder = CatalogBuilder()
    cdp_catalog_builder = CatalogBuilder()

    for trace_number in itertools.count():
        progress_callback(_READ_PROPORTION * pos_begin / length)
        fh.seek(pos_begin)
        data = fh.read(TRACE_HEADER_NUM_BYTES)
        if len(data) < TRACE_HEADER_NUM_BYTES:
            break
        trace_header = TraceHeader._make(trace_header_format.unpack(data))
        num_samples = trace_header.ns
        trace_length_catalog_builder.add(trace_number, num_samples)
        samples_bytes = num_samples * bps
        trace_offset_catalog_builder.add(trace_number, pos_begin)
        # Should we check the data actually exists?
        line_catalog_builder.add((trace_header.Inline3D,
                                  trace_header.Crossline3D),
                                 trace_number)
        cdp_catalog_builder.add(trace_header.cdp, trace_number)
        pos_end = pos_begin + TRACE_HEADER_NUM_BYTES + samples_bytes
        pos_begin = pos_end

    progress_callback(_READ_PROPORTION)

    trace_offset_catalog = trace_offset_catalog_builder.create()
    progress_callback(_READ_PROPORTION + (_READ_PROPORTION / 4))

    trace_length_catalog = trace_length_catalog_builder.create()
    progress_callback(_READ_PROPORTION + (_READ_PROPORTION / 2))

    cdp_catalog = cdp_catalog_builder.create()
    progress_callback(_READ_PROPORTION + (_READ_PROPORTION * 3 / 4))

    line_catalog = line_catalog_builder.create()
    progress_callback(1)

    return (trace_offset_catalog,
            trace_length_catalog,
            cdp_catalog,
            line_catalog)

def read_trace_header(fh, trace_header_format, pos=None):
    """Read a trace header.

    Args:
        fh: A file-like-object open in binary mode.

        trace_header_format: A Struct object, such as obtained from a
            call to compile_trace_header_format()

        pos: The file offset in bytes from the beginning from which the data
            is to be read.

    Returns:
        A TraceHeader object.
    """
    if pos is not None:
        fh.seek(pos)
    data = fh.read(TRACE_HEADER_NUM_BYTES)
    trace_header = TraceHeader._make(
        trace_header_format.unpack(data))
    return trace_header


def read_binary_values(fh, pos=None, ctype='l', count=1, endian='>'):
    """Read a series of values from a binary file.

    Args:
        fh: A file-like-object open in binary mode.

c

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
        raise EOFError("{} bytes requested but only {} available".format(
            block_size, len(buf)))

    values = (unpack_ibm_floats(buf, count)
              if fmt == 'ibm'
              else unpack_values(buf, count, item_size, fmt))
    assert len(values) == count
    return values


def unpack_ibm_floats(data, count):
    """Unpack a series of binary-encoded big-endian single-precision IBM floats.

    Args:
        data: A sequence of bytes. (Python 2 - a str object,
            Python 3 - a bytes object)

        count: The number of floats to be read.

    Returns:
        A sequence of floats.
    """
    return array('f', (ibm2ieee(data[i: i+4]) for i in range(0, count * 4, 4)))


def unpack_values(buf, count, item_size, fmt, endian='>'):
    """Unpack a series items from a byte string.

    Args:
        data: A sequence of bytes. (Python 2 - a str object,
            Python 3 - a bytes object)

        count: The number of floats to be read.

        fmt: A format code (one of the values in the datatype.CTYPES
            dictionary)

        endian: '>' for big-endian data (the standard and default), '<'
            for little-endian (non-standard)

    Returns:
        A sequence of objects with type corresponding to the format code.
    """
    c_format = '{}{}{}'.format(endian, count, fmt)
    return struct.unpack(c_format, buf)
    # We could use array.fromfile() here. On the one hand it's likely
    # to be faster and more compact,
    #
    # On the other, it only works on "real" files, not arbitrary
    # file-like-objects and it would require us to handle endian byte
    # swapping ourselves.


def write_trace_header(fh, trace_header, trace_header_format, pos=None):
    """Write a TraceHeader to file.

    Args:
        fh: A file-like object open in binary mode for writing.

        trace_header: A TraceHeader object.

        trace_header_format: A Struct object, such as obtained from a
            call to compile_trace_header_format()

        pos: An optional file offset in bytes from the beginning of the
            file. Defaults to the current file position.
    """
    if pos is not None:
        fh.seek(pos, os.SEEK_SET)
    buf = trace_header_format.pack(trace_header)
    fh.write(buf)


def write_trace_values(fh, values, ctype='l', pos=None):
    write_binary_values(fh, values, ctype, pos)


def write_binary_values(fh, values, ctype='l', pos=None):
    """Write a series on values to a file.

    Args:
        fh: A file-like-object open for writing in binary mode.

        values: An iterable series of values.

        ctype: The SEG Y data type.

        pos: An optional offset from the beginning of the file. If omitted,
            any writing is done at the current file position.
    """
    fmt = CTYPES[ctype]

    if pos is not None:
        fh.seek(pos, os.SEEK_SET)

    buf = (pack_ibm_floats(values)
           if fmt == 'ibm'
           else pack_values(values, fmt))

    fh.write(buf)


def pack_ibm_floats(values):
    """Pack floats into binary-encoded big-endian single-precision IBM floats.

    Args:
        values: An iterable series of numeric values.

    Returns:
        A sequence of bytes. (Python 2 - a str object, Python 3 - a bytes
            object)
    """
    return EMPTY_BYTE_STRING.join(ieee2ibm(value) for value in values)


def pack_values(values, fmt, endian='>'):
    """Pack values into binary encoded big-endian byte strings.

    Args:
        values: An iterable series of values.

        fmt: A format code (one of the values in the datatype.CTYPES
            dictionary)

        endian: '>' for big-endian data (the standard and default), '<'
            for little-endian (non-standard)
    """
    c_format = '{}{}{}'.format(endian, len(values), fmt)
    return struct.pack(c_format, *values)


_TraceAttributeSpec = namedtuple('Record', ['name', 'pos', 'type'])


def compile_trace_header_format(endian='>'):
    """Compile a format string for use with the struct module from the
    trace header definition.

    Args:
        endian: '>' for big-endian data (the standard and default), '<' for
            little-endian (non-standard)

    Returns:
        A string which can be used with the struct module for parsing
        trace headers.

    """

    record_specs = sorted(
        [_TraceAttributeSpec(name,
                             TRACE_HEADER_DEF[name]['pos'],
                             TRACE_HEADER_DEF[name]['type'])
         for name in TRACE_HEADER_DEF],
        key=lambda r: r.pos)

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
    record_specs = sorted(
        [_TraceAttributeSpec(name,
                             TRACE_HEADER_DEF[name]['pos'],
                             TRACE_HEADER_DEF[name]['type'])
         for name in TRACE_HEADER_DEF],
        key=lambda r: r.pos)
    return namedtuple('TraceHeader',
                      (record_spec.name for record_spec in record_specs))


TraceHeader = _compile_trace_header_record()
