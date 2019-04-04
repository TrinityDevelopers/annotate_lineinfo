"""
Annotate IDA with source and line number information from a PDB

Copyright (c) 2019 Branden Clark [github.com/clarkb7]
MIT License, see LICENSE for details.
"""

import os
import sys
import argparse

import logging
logger = logging.getLogger(__name__)

##================ HELPER UTILITIES ================##
def compiland_name(compiland):
    """"""
    if compiland.sourceFileName is None:
        return os.path.basename(compiland.name)
    return compiland.sourceFileName

def dia_enum_iter(enum):
    """Turn an IDiaEnum* object into a python generator"""
    for i in xrange(enum.count):
        yield enum.Next(1)[0]

##================ DIA ================##
class DIASession(object):
    DEFAULT_MSDIA_VERSION="msdia140"

    def __init__(self,binary,msdia_ver=None):
        """Initialize MSDIA com API session"""
        logging.getLogger("comtypes").setLevel(logging.WARNING)
        from comtypes.client import GetModule, CreateObject
        from ctypes.util import find_library
        import ctypes
        import _ctypes

        if msdia_ver is None:
            msdia_ver = type(self).DEFAULT_MSDIA_VERSION

        self.logger = logging.getLogger(type(self).__name__)

        # Find path to dia lib
        dllpath = find_library(msdia_ver)
        if dllpath is None:
            raise ValueError("Could not find {}.dll".format(msdia_ver))
        self.logger.debug("Found {} at {}".format(msdia_ver, dllpath))
        # Ready comtypes interface
        self.msdia = GetModule(dllpath)
        self.dataSource = CreateObject(self.msdia.DiaSource, interface=self.msdia.IDiaDataSource)
        # Load debug info
        ext = os.path.splitext(binary)[1]
        try:
            if ext == '.pdb':
                self.dataSource.loadDataFromPdb(binary)
            else:
                self.dataSource.loadDataForExe(binary,os.path.dirname(binary), None)
        except _ctypes.COMError as e:
            hr = ctypes.c_uint(e[0]).value
            if hr == 0x806D0005: # E_PDB_NOT_FOUND
                msg = "Unable to locate PDB"
            elif hr == 0x806D0012: # E_PDB_FORMAT
                msg = "Invalid or obsolete file format"
            else:
                msg = "Unknown exception loading PDB info: {}".format(e)
            raise ValueError(msg)
        self.session = self.dataSource.openSession()

    def iter_functions(self):
        """Iterate all function symbols"""
        enumcomp = self.session.globalScope.findChildren(self.msdia.SymTagCompiland, None, 0)
        for comp in dia_enum_iter(enumcomp):
            self.logger.debug("--------------- {} ---------------".format(compiland_name(comp)))
            enumfunc = comp.findChildren(self.msdia.SymTagFunction, None, 0)
            for func in dia_enum_iter(enumfunc):
                yield func

    def iter_lineinfo_by_rva(self, rva, length):
        """Iterate lines includes in range [rva:rva+length]"""
        enumlines = self.session.findLinesByRVA(rva, length)
        for line in dia_enum_iter(enumlines):
            yield line

    def iter_function_lineinfo(self):
        """Iterate functions and their contained line info"""
        for func in self.iter_functions():
            for line in self.iter_lineinfo_by_rva(func.relativeVirtualAddress, func.length):
                self.logger.debug("[{:08X}-{:08X}] {}:{}:{}".format(
                    line.relativeVirtualAddress, line.relativeVirtualAddress+line.length,
                    compiland_name(line.compiland), func.name, line.lineNumber))
                yield func,line

def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', help="Path to exe or pdb to analyze")
    parser.add_argument("-v", "--verbose",action="store_true")
    parser.add_argument("--msdia",help="msdia version to use (default: %(default)s)",
        default=DIASession.DEFAULT_MSDIA_VERSION)
    args = parser.parse_args(argv)

    logging.basicConfig(format='%(asctime)s | %(name)s | %(levelname)s | %(message)s')

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        ds = DIASession(args.binary,msdia_ver=args.msdia)
    except ValueError as e:
        logger.error(e)
        exit(1)
    else:
        for _ in ds.iter_function_lineinfo():
            pass

try:
    import idaapi
except ImportError:
    # No IDA here
    if __name__ == "__main__":
        main(sys.argv[1:])
else:
    ##================ Run from within IDA ================##

    def ida_anterior_comment(ea, comment):
        """Add anterior comment @comment at @ea"""
        # Ensure we don't duplcate the comment
        cur_cmt = idaapi.get_extra_cmt(ea, idaapi.E_PREV)
        if cur_cmt is not None and comment in cur_cmt:
            return
        # Add the comment
        idaapi.add_long_cmt(ea, True, comment)

    def ida_add_lineinfo_comment(line, func=None):
        ea = idaapi.get_imagebase()+line.relativeVirtualAddress
        cmt = "{}".format(compiland_name(line.compiland))
        if func is not None:
            cmt += ":{}".format(func.name)
        cmt += ":{}".format(line.lineNumber)
        ida_anterior_comment(ea, cmt)

    def ida_add_lineinfo_comment_to_range(dia, ea, length):
        rva = ea-idaapi.get_imagebase()
        for line in dia.iter_lineinfo_by_rva(rva, length):
            ida_add_lineinfo_comment(line)

    def ida_add_lineinfo_comment_to_func(dia, ida_func):
        length = ida_func.size()+1
        ida_add_lineinfo_comment_to_range(dia, ida_func.startEA, length)

    def ida_annotate_lineinfo_dia(dia, include_function_name=True):
        for func,line in dia.iter_function_lineinfo():
            ida_add_lineinfo_comment(line, func=func if include_function_name else None)

    def ida_annotate_lineinfo(binary=None, msdia_ver=None,
        include_function_name=True):
        """Annotate IDA with source/line number information for @binary"""
        if binary is None:
            binary = idaapi.get_input_file_path()
        ds = DIASession(binary,msdia_ver=msdia_ver)
        ida_annotate_lineinfo_dia(ds, include_function_name=include_function_name)

    if __name__ == "__main__":
        ida_annotate_lineinfo()
