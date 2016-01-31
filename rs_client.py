#!/usr/bin/env python3
'''
script that generates rust code from xcb xml definitions
Each invokation of this script generates one ffi file and one
rust file for an extension or the main X Protocol.

Usage: ./rs_client.py -o src xml/xproto.xml
'''

import sys
import os
import time
import re



class SourceFile(object):
    '''
    buffer to append code in various sections of a file
    in any order
    '''

    _one_indent_level = '    '

    def __init__(self):
        self._section = 0
        self._lines = []
        self._indents = []

    def getsection(self):
        return self._section

    def section(self, section):
        '''
        Set the section of the file where to append code.
        Allows to make different sections in the file to append
        to in any order
        '''
        while len(self._lines) <= section:
            self._lines.append([])
        while len(self._indents) <= section:
            self._indents.append(0)
        self._section = section

    def getindent(self):
        '''
        returns indentation of the current section
        '''
        return self._indents[self._section]

    def setindent(self, indent):
        '''
        sets indentation of the current section
        '''
        self._indents[self._section] = indent;

    def indent_block(self):
        class Indenter(object):
            def __init__(self, sf):
                self.sf = sf
            def __enter__(self):
                self.sf.indent()
            def __exit__(self, type, value, traceback):
                self.sf.unindent()
        return Indenter(self)

    def indent(self):
        '''
        adds one level of indentation to the current section
        '''
        self._indents[self._section] += 1

    def unindent(self):
        '''
        removes one level of indentation to the current section
        '''
        assert self.getindent() > 0, "negative indent"
        self._indents[self._section] -= 1

    def __call__(self, fmt, *args):
        '''
        Append a line to the file at in its current section and
        indentation of the current section
        '''
        indent = SourceFile._one_indent_level * self._indents[self._section]
        self._lines[self._section].append(indent + (fmt % args))


    def writeout(self, path):
        with open(path, 'w') as f:
            for section in self._lines:
                for line in section:
                    print(line.rstrip(), file=f)


# FFI source file
_f = SourceFile()

# Rust interface file
_r = SourceFile()

# utility to add same code in both files
def _rf(fmt, *args):
    _f(fmt, *args)
    _r(fmt, *args)


_ns = None
_ext_names = {}

# global variable to keep track of serializers and
# switch data types due to weird dependencies
finished_serializers = []
finished_sizeof = []
finished_switch = []

_types_uneligible_to_copy = []

# current handler is used for error reporting
current_handler = None


# exported functions to xcbgen start by 'rs_'

# starting with opening and closing

def rs_open(module):
    '''
    Handles module open.
    module is a xcbgen.state.Module object
    '''
    global _ns
    _ns = module.namespace

    EnumCodegen.build_collision_table(module)

    linklib = "xcb"
    if _ns.is_ext:
        linklib = linklib + '-' + _ns.header
        _ext_names[_ns.ext_name.lower()] = _ns.header
        for (n, h) in module.direct_imports:
            _ext_names[n.lower()] = h

    _r.section(0)
    _f.section(0)
    _rf('// edited from %s by rs_client.py on %s',
            _ns.file, time.strftime('%c'))
    _rf('// do not edit!')
    _rf('')

    _f('')
    _f('#![allow(improper_ctypes)]')
    _f('')
    _f('use ffi::base::*;')

    if _ns.is_ext:
        for (n, h) in module.direct_imports:
            _f('use ffi::%s::*;', h)
        _f('')
    _f('use libc::{c_char, c_int, c_uint, c_void};')
    _f('use std;')

    if _ns.is_ext:
        _f('pub const XCB_%s_MAJOR_VERSION: u32 = %s;',
                    _ns.ext_name.upper(),
                    _ns.major_version)
        _f('pub const XCB_%s_MINOR_VERSION: u32 = %s;',
                    _ns.ext_name.upper(),
                    _ns.minor_version)

    _f.section(1)
    _f('')
    _f('')
    _f('#[link(name="%s")]', linklib)
    _f('extern {')
    _f.indent()
    if _ns.is_ext:
        _f('')
        _f('static %s: xcb_extension_t;', _ffi_name(_ns.prefix + ('id',)))

    _r('')
    _r('use base;')
    _r('use ffi::base::*;')
    _r('use ffi::%s::*;', _ns.header)
    if _ns.is_ext:
        for (n, h) in module.direct_imports:
            _r('use ffi::%s::*;', h)
            _r('use %s;', h)
    _r('use libc::{self, c_char, c_int, c_uint, c_void};')
    _r('use std;')
    _r('use std::option::Option;')
    _r('use std::iter::Iterator;')

    _r.section(1)
    _r('')
    _r('')



def rs_close(module):
    '''
    Handles module close.
    module is a xcbgen.state.Module object.
    main task is to write the files out
    '''

    _f.section(1)

    _f('')
    _f.unindent()
    _f('} // extern')

    _f.writeout(os.path.join(module.rs_srcdir, "ffi", "%s.rs" % _ns.header))
    _r.writeout(os.path.join(module.rs_srcdir, "%s.rs" % _ns.header))



# transformation of name tuples

_cname_re = re.compile('([A-Z0-9][a-z]+|[A-Z0-9]+(?![a-z])|[a-z]+)')
_rs_keywords = ['type', 'str', 'match']


def _tit_split(string):
    '''
    splits string with '_' on each titlecase letter
    >>> _tit_split('SomeString')
    Some_String
    >>> _tit_split('WINDOW')
    WINDOW
    '''
    split = _cname_re.finditer(string)
    name_parts = [match.group(0) for match in split]
    return '_'.join(name_parts)

def _tit_cap(string):
    '''
    capitalize each substring beggining by a titlecase letter
    >>> _tit_cap('SomeString')
    SomeString
    >>> _tit_cap('WINDOW')
    Window
    '''
    split = _cname_re.finditer(string)
    name_parts = [match.group(0) for match in split]
    name_parts = [i[0].upper() + i[1:].lower() for i in name_parts]
    return ''.join(name_parts)

def _symbol(string):
    if string in _rs_keywords:
        string += '_'
    return string

def _upper_1st(string):
    '''
    return copy of string with first letter turned into upper.
    Other letters are untouched.
    '''
    if len(string) == 0:
        return ''
    if len(string) == 1:
        return string.upper()
    return string[0].upper() + string[1:]

def _upper_name(nametup):
    '''
    return a string made from a nametuple with all upper case
    joined with underscore
    >>> _upper_name(('xcb', 'constant', 'AwesomeValue'))
    XCB_CONSTANT_AWESOME_VALUE
    '''
    return '_'.join(tuple(_tit_split(name) for name in nametup)).upper()

def _cap_name(nametup):
    '''
    return a string made from a nametuple with joined title case
    >>> _cap_name(('xcb', 'Type', 'Name'))
    XcbTypeName
    >>> _cap_name(('xcb', 'TypeName'))
    XcbTypeName
    >>> _cap_name(('xcb', 'TYPENAME'))
    XcbTypename
    '''
    return ''.join(tuple(_upper_1st(name) for name in nametup))

def _lower_name(nametup):
    '''
    return a string made from a nametuple with all lower case
    joined with underscore
    >>> _upper_name(('xcb', 'Ext', 'RequestName'))
    xcb_ext_request_name
    '''
    return '_'.join(tuple(_tit_split(name) for name in nametup)).lower()


def _ext_nametup(nametup):
    '''
    return the nametup with 2nd name lowered if module is an extension
    >>> _ext_nametup(('u32',))
    ('u32',)
    >>> _ext_nametup(('xcb', 'XprotoType'))
    ('xcb', 'XprotoType')
    >>> _ext_nametup(('xcb', 'RandR', 'SuperType'))
    ('xcb', 'randr', 'SuperType')
    '''
    if len(nametup) > 2 and nametup[1] in _ext_names:
        nametup = tuple(_ext_names[name] if i == 1 else name
                for (i, name) in enumerate(nametup))
        # lowers extension to avoid '_' split with title letters
        #nametup = tuple(name.lower() if i == 1 else name
        #        for (i, name) in enumerate(nametup))
    return nametup

def _ffi_type_name(nametup):
    '''
    turns the nametup into a FFI type
    >>> _ffi_type_name(('u32',))
    u32
    >>> _ffi_type_name(('xcb', 'XprotoType'))
    xcb_xproto_type_t
    >>> _ffi_type_name(('xcb', 'RandR', 'SuperType'))
    xcb_randr_super_type_t
    '''
    if len(nametup) == 1:
        # handles SimpleType
        return nametup[0]
    return _ffi_name(nametup + ('t',))


def _ffi_name(nametup):
    '''
    turns the nametup into a FFI name
    >>> _ffi_type_name(('u32',))
    u32
    >>> _ffi_type_name(('xcb', 'XprotoType', 't'))
    xcb_xproto_type_t
    >>> _ffi_type_name(('xcb', 'RandR', 'SuperType', 't'))
    xcb_randr_super_type_t
    '''
    secondIsExt = (len(nametup) > 2 and nametup[1] in _ext_names)
    nametup = _ext_nametup(nametup)

    if secondIsExt:
        return '_'.join(tuple(name if i==1 else _tit_split(name)
                for (i, name) in enumerate(nametup))).lower()
    else:
        return '_'.join(tuple(_tit_split(name) for name in nametup)).lower()



def _rs_extract_module(nametup):
    '''
    returns the module extracted from nametup
    along with the nametup without the module parts
    if module is local module, an empty module is returned
    >>> _rs_type_name(('u32',))
    ("", "u32")
    >>> _rs_type_name(('xcb', 'Type'))
    ("xproto::", "Type")
    >>> _rs_type_name(('xcb', 'RandR', 'SuperType'))
    ("randr::", "SuperType")
    '''
    # handles SimpleType
    if len(nametup) == 1:
        return ("", nametup[0])

    # remove 'xcb'
    if nametup[0].lower() == 'xcb':
        nametup = nametup[1:]

    module = ''
    # handle extension type
    if nametup[0].lower() in _ext_names:
        ext = _ext_names[nametup[0].lower()]
        if (not _ns.is_ext or
                ext != _ns.header):
            module = ext + '::'
        nametup = nametup[1:]

    # handle xproto type
    else:
        if _ns.is_ext:
            module = 'xproto::'

    return (module, nametup)



def _rs_type_name(nametup):
    '''
    turns the nametup into a Rust type name
    foreign rust type names include module prefix
    >>> _rs_type_name(('u32',))
    u32
    >>> _rs_type_name(('xcb', 'Type'))
    xproto::Type
    >>> _rs_type_name(('xcb', 'RandR', 'SuperType'))
    randr::SuperType
    '''
    if len(nametup) == 1:
        return nametup[0]

    (module, nametup) = _rs_extract_module(nametup)

    return module + ''.join([_tit_cap(n) for n in nametup])


def _rs_name(nametup):

    (module, nametup) = _rs_extract_module(nametup)

    return module + '_'.join([_tit_split(n) for n in nametup]).lower()




# FFI codegen functions

def _ffi_type_setup(typeobj, nametup, suffix=()):
    '''
    Sets up all the C-related state by adding additional data fields to
    all Field and Type objects.  Here is where we figure out most of our
    variable and function names.

    Recurses into child fields and list member types.
    '''
    # Do all the various names in advance
    typeobj.ffi_type = _ffi_type_name(nametup + suffix)

    typeobj.ffi_iterator_type = _ffi_type_name(nametup + ('iterator',))
    typeobj.ffi_next_fn = _ffi_name(nametup + ('next',))
    typeobj.ffi_end_fn = _ffi_name(nametup + ('end',))

    typeobj.ffi_request_fn = _ffi_name(nametup)
    typeobj.ffi_checked_fn = _ffi_name(nametup + ('checked',))
    typeobj.ffi_unchecked_fn = _ffi_name(nametup + ('unchecked',))
    typeobj.ffi_reply_fn = _ffi_name(nametup + ('reply',))
    typeobj.ffi_reply_type = _ffi_type_name(nametup + ('reply',))
    typeobj.ffi_cookie_type = _ffi_type_name(nametup + ('cookie',))
    typeobj.ffi_reply_fds_fn = _ffi_name(nametup + ('reply_fds',))

    typeobj.ffi_need_aux = False
    typeobj.ffi_need_serialize = False
    typeobj.ffi_need_sizeof = False

    typeobj.ffi_aux_fn = _ffi_name(nametup + ('aux',))
    typeobj.ffi_aux_checked_fn = _ffi_name(nametup + ('aux', 'checked'))
    typeobj.ffi_aux_unchecked_fn = _ffi_name(nametup + ('aux', 'unchecked'))
    typeobj.ffi_serialize_fn = _ffi_name(nametup + ('serialize',))
    typeobj.ffi_unserialize_fn = _ffi_name(nametup + ('unserialize',))
    typeobj.ffi_unpack_fn = _ffi_name(nametup + ('unpack',))
    typeobj.ffi_sizeof_fn = _ffi_name(nametup + ('sizeof',))

    # special case: structs where variable size fields are followed
    # by fixed size fields
    typeobj.ffi_var_followed_by_fixed_fields = False

    if not typeobj.fixed_size():
        if not typeobj in _types_uneligible_to_copy:
            _types_uneligible_to_copy.append(typeobj)
        if hasattr(typeobj, 'parents'):
            for p in typeobj.parents:
                _types_uneligible_to_copy.append(p)


    if typeobj.is_switch:
        typeobj.ffi_need_serialize = True
        for bitcase in typeobj.bitcases:
            bitcase.ffi_field_name = _symbol(bitcase.field_name)
            bitcase_name = (bitcase.field_type if bitcase.type.has_name
                    else nametup)
            _ffi_type_setup(bitcase.type, bitcase_name, ())

    elif typeobj.is_container:

        prev_varsized_field = None
        prev_varsized_offset = 0
        first_field_after_varsized = None

        for field in typeobj.fields:
            _ffi_type_setup(field.type, field.field_type, ())
            if field.type.is_list:
                _ffi_type_setup(field.type.member, field.field_type, ())
                if (field.type.nmemb is None):
                    typeobj.ffi_need_sizeof = True

            field.ffi_field_type = _ffi_type_name(field.field_type)
            field.ffi_field_name = _symbol(field.field_name)
            field.has_subscript = (field.type.nmemb and
                            field.type.nmemb > 1)
            field.ffi_need_const = (field.type.nmemb != 1)
            field.ffi_need_pointer = (field.type.nmemb != 1)

            # correct the need_pointer field for variable size non-list types
            if not field.type.fixed_size():
                field.ffi_need_pointer = True
            if field.type.is_list and not field.type.member.fixed_size():
                field.ffi_need_pointer = True

            if field.type.is_switch:
                field.ffi_need_const = True
                field.ffi_need_pointer = True
                field.ffi_need_aux = True
            elif not field.type.fixed_size() and not field.type.is_bitcase:
                typeobj.ffi_need_sizeof = True

            field.ffi_iterator_type = _ffi_type_name(
                    field.field_type + ('iterator',))
            field.ffi_iterator_fn = _ffi_name(
                    nametup + (field.field_name, 'iterator'))
            field.ffi_accessor_fn = _ffi_name(
                    nametup + (field.field_name,))
            field.ffi_length_fn = _ffi_name(
                    nametup + (field.field_name, 'length'))
            field.ffi_end_fn = _ffi_name(
                    nametup + (field.field_name, 'end'))

            field.prev_varsized_field = prev_varsized_field
            field.prev_varsized_offset = prev_varsized_offset

            if prev_varsized_offset == 0:
                first_field_after_varsized = field
            field.first_field_after_varsized = first_field_after_varsized

            if field.type.fixed_size():
                prev_varsized_offset += field.type.size
                # special case: intermixed fixed and variable size fields
                if (prev_varsized_field is not None and
                        not field.type.is_pad and field.wire):
                    if not typeobj.is_union:
                        typeobj.ffi_need_serialize = True
                        typeobj.ffi_var_followed_by_fixed_fields = True
            else:
                typeobj.last_varsized_field = field
                prev_varsized_field = field
                prev_varsized_offset = 0

            if typeobj.ffi_var_followed_by_fixed_fields:
                if field.type.fixed_size():
                    field.prev_varsized_field = None

    if typeobj.ffi_need_serialize:
        # when _unserialize() is wanted, create _sizeof() as well
        # for consistency reasons
        typeobj.ffi_need_sizeof = True

    # as switch does never appear at toplevel,
    # continue here with type construction
    if typeobj.is_switch:
        if typeobj.ffi_type not in finished_switch:
            finished_switch.append(typeobj.ffi_type)
            # special: switch C structs get pointer fields
            # for variable-sized members
            _ffi_struct(typeobj)
            for bitcase in typeobj.bitcases:
                bitcase_name = (bitcase.type.name if bitcase.type.has_name
                    else nametup)
                _ffi_accessors(bitcase.type, bitcase_name)
                # no list with switch as element, so no call to
                # _c_iterator(field.type, field_name) necessary

    if not typeobj.is_bitcase:
        if typeobj.ffi_need_serialize:
            if typeobj.ffi_serialize_fn not in finished_serializers:
                finished_serializers.append(typeobj.ffi_serialize_fn)
                #_ffi_serialize('serialize', typeobj)

                # _unpack() and _unserialize() are only needed
                # for special cases:
                #   switch -> unpack
                #   special cases -> unserialize
                if (typeobj.is_switch or
                        typeobj.ffi_var_followed_by_fixed_fields):
                    pass
                    #_ffi_serialize('unserialize', typeobj)

        if typeobj.ffi_need_sizeof:
            if typeobj.ffi_sizeof_fn not in finished_sizeof:
                if not _ns.is_ext or typeobj.name[:2] == _ns.prefix:
                    finished_sizeof.append(typeobj.ffi_sizeof_fn)
                    #_ffi_serialize('sizeof', typeobj)




def _ffi_bitcase_name(switch, bitcase):
    assert switch.is_switch and bitcase.type.has_name
    switch_name = _lower_name(_ext_nametup(switch.name))
    return '_%s__%s' % (switch_name, bitcase.ffi_field_name)


def _ffi_struct(typeobj, must_pack=False):
    '''
    Helper function for handling all structure types.
    Called for structs, requests, replies, events, errors...
    '''

    struct_fields = []

    for field in typeobj.fields:
        if (not field.type.fixed_size()
            and not typeobj.is_switch
            and not typeobj.is_union):
            continue
        if field.wire:
            struct_fields.append(field)

    _f.section(0)
    _f('')
    _f('#[repr(C%s)]', ', packed' if must_pack else '')
    _f('pub struct %s {', typeobj.ffi_type)
    _f.indent()

    maxfieldlen = 0
    if not typeobj.is_switch:
        for field in typeobj.fields:
            maxfieldlen = max(maxfieldlen, len(field.ffi_field_name))
    else:
        for b in typeobj.bitcases:
            if b.type.has_name:
                maxfieldlen = max(maxfieldlen, len(b.ffi_field_name))
            else:
                for field in b.type.fields:
                    maxfieldlen = max(maxfieldlen, len(field.ffi_field_name))



    def _ffi_struct_field(field):
        ftype = field.ffi_field_type
        space = ' '* (maxfieldlen - len(field.ffi_field_name))
        if (field.type.fixed_size() or typeobj.is_union or
            # in case of switch with switch children,
            # don't make the field a pointer
            # necessary for unserialize to work
            (typeobj.is_switch and field.type.is_switch)):
            if field.has_subscript:
                ftype = '[%s; %d]' % (ftype, field.type.nmemb)
            _f('pub %s: %s%s,', field.ffi_field_name, space, ftype)
        else:
            assert not field.has_subscript
            _f('pub %s: %s*mut %s,', field.ffi_field_name, space, ftype)

    named_bitcases = []

    if not typeobj.is_switch:
        for field in struct_fields:
            _ffi_struct_field(field)
    else:
        for b in typeobj.bitcases:
            if b.type.has_name:
                named_bitcases.append(b)
                space = ' ' * (maxfieldlen - len(b.ffi_field_name))
                _f('pub %s: %s%s,', b.ffi_field_name, space,
                        _ffi_bitcase_name(typeobj, b))
            else:
                for field in b.type.fields:
                    _ffi_struct_field(field)

    _f.unindent()
    _f('}')
    if not typeobj in _types_uneligible_to_copy:
        _f('')
        _f('impl Copy for %s {}', typeobj.ffi_type)
        _f('impl Clone for %s {', typeobj.ffi_type)
        _f('    fn clone(&self) -> %s { *self }', typeobj.ffi_type)
        _f('}')

    for b in named_bitcases:
        _f('')
        _f('#[repr(C)]')
        _f('pub struct %s {', _ffi_bitcase_name(typeobj, b))
        _f.indent()
        maxfieldlen = 0
        for field in b.type.fields:
            maxfieldlen = max(maxfieldlen, len(field.ffi_field_name))
        for field in b.type.fields:
            _ffi_struct_field(field)
        _f.unindent()
        _f('}')



def _ffi_accessors_list(typeobj, field):
    '''
    Declares the accessor functions for a list field.
    Declares a direct-accessor function only if the list members
        are fixed size.
    Declares length and get-iterator functions always.
    '''

    list = field.type
    ffi_type = typeobj.ffi_type

    # special case: switch
    # in case of switch, 2 params have to be supplied to certain
    # accessor functions:
    #   1. the anchestor object (request or reply)
    #   2. the (anchestor) switch object
    # the reason is that switch is either a child of a request/reply
    # or nested in another switch,
    # so whenever we need to access a length field, we might need to
    # refer to some anchestor type
    switch_obj = typeobj if typeobj.is_switch else None
    if typeobj.is_bitcase:
        switch_obj = typeobj.parents[-1]
    if switch_obj is not None:
        ffi_type = switch_obj.ffi_type

    params = []
    parents = typeobj.parents if hasattr(typeobj, 'parents') else [typeobj]
    # 'R': parents[0] is always the 'toplevel' container type
    params.append(('R: *const %s' % parents[0].ffi_type, parents[0]))
    # auxiliary object for 'R' parameters
    R_obj = parents[0]

    if switch_obj is not None:
        # now look where the fields are defined that are needed to evaluate
        # the switch expr, and store the parent objects in accessor_params and
        # the fields in switch_fields

        # 'S': name for the 'toplevel' switch
        toplevel_switch = parents[1]
        params.append(('S: *const %s' % toplevel_switch.ffi_type,
                toplevel_switch))

        # auxiliary object for 'S' parameter
        S_obj = parents[1]

    _f.section(1)
    if list.member.fixed_size():
        idx = 1 if switch_obj is not None else 0
        _f('')
        _f('pub fn %s (%s)', field.ffi_accessor_fn, params[idx][0])
        _f('        -> *mut %s;', field.ffi_field_type)

    def _may_switch_fn(fn_name, return_type):
        _f('')
        if switch_obj is not None:
            fn_start = 'pub fn %s (' % fn_name
            spacing = ' '*len(fn_start)
            _f('%sR: *const %s,', fn_start, R_obj.ffi_type)
            _f('%sS: *const %s)', spacing, S_obj.ffi_type)
            _f('        -> %s;', return_type)
        else:
            _f('pub fn %s (R: *const %s)', fn_name, ffi_type)
            _f('        -> %s;', return_type)

    _may_switch_fn(field.ffi_length_fn, 'c_int')

    if field.type.member.is_simple:
        _may_switch_fn(field.ffi_end_fn, 'xcb_generic_iterator_t')
    else:
        _may_switch_fn(field.ffi_iterator_fn, field.ffi_iterator_type)



def _ffi_accessors_field(typeobj, field):
    '''
    Declares the accessor functions for a non-list field that follows
    a variable-length field.
    '''
    ffi_type = typeobj.ffi_type

    # special case: switch
    switch_obj = typeobj if typeobj.is_switch else None
    if typeobj.is_bitcase:
        switch_obj = typeobj.parents[-1]
    if switch_obj is not None:
        ffi_type = switch_obj.ffi_type

    _f.section(1)
    if field.type.is_simple:
        _f('')
        _f('pub fn %s (R: *const %s)', field.ffi_accessor_fn, ffi_type)
        _f('        -> %s;', field.ffi_field_type)
    else:
        if field.type.is_switch and switch_obj is None:
            return_type = '*mut c_void'
        else:
            return_type = '*mut %s' % field.ffi_field_type

        _f('')
        _f('pub fn %s (R: *const %s)', field.ffi_accessor_fn, ffi_type)
        _f('        -> %s;', return_type)


def _ffi_accessors(typeobj, nametup):
    for field in typeobj.fields:
        if not field.type.is_pad:
            if field.type.is_list and not field.type.fixed_size():
                _ffi_accessors_list(typeobj, field)
            elif (field.prev_varsized_field is not None
                    or not field.type.fixed_size()):
                _ffi_accessors_field(typeobj, field)


def _ffi_iterator(typeobj, nametup):

    lifetime = "<'a>" if typeobj.has_lifetime else ""

    _f.section(0)
    _f('')
    _f('#[repr(C)]')
    _f("pub struct %s%s {", typeobj.ffi_iterator_type, lifetime)
    _f('    pub data:  *mut %s,', typeobj.ffi_type)
    _f('    pub rem:   c_int,')
    _f('    pub index: c_int,')
    if typeobj.has_lifetime:
        _f("    _phantom:  std::marker::PhantomData<&'a %s>,", typeobj.ffi_type)
    _f('}')

    _f.section(1)
    _f('')
    _f('pub fn %s (i: *mut %s);', typeobj.ffi_next_fn,
            typeobj.ffi_iterator_type)

    _f('')
    _f('pub fn %s (i: *mut %s)', typeobj.ffi_end_fn,
            typeobj.ffi_iterator_type)
    _f('        -> xcb_generic_iterator_t;')




def _ffi_reply(request):
    '''
    Declares the function that returns the reply structure.
    '''
    _f.section(1)
    _f('')
    _f('/// the returned value must be freed by the caller using ' +
            'libc::free().')
    fn_start = 'pub fn %s (' % request.ffi_reply_fn
    spacing = ' ' * len(fn_start)
    _f('%sc:      *mut xcb_connection_t,', fn_start)
    _f('%scookie: %s,', spacing, request.ffi_cookie_type)
    _f('%serror:  *mut *mut xcb_generic_error_t)', spacing)
    _f('        -> *mut %s;', request.ffi_reply_type)


def _ffi_reply_has_fds(self):
    for field in self.fields:
        if field.isfd:
            return True
    return False


def _ffi_reply_fds(request, name):
    '''
    Declares the function that returns fds related to the reply.
    '''
    _f.section(1)
    _f('')
    _f('/// the returned value must be freed by the caller using ' +
            'libc::free().')
    fn_start = 'pub fn %s (' % request.ffi_reply_fds_fn
    spacing = ' ' * len(fn_start)
    _f('%sc:     *mut xcb_connection_t,', fn_start)
    _f('%sreply: %s)', spacing, request.ffi_cookie_type)
    _f('        -> *mut c_int;')



# Rust codegen function

def _rs_type_setup(typeobj, nametup, suffix=()):
    #assert typeobj.hasattr('ffi_type')

    typeobj.rs_type = _rs_type_name(nametup + suffix)

    typeobj.rs_iterator_type = _rs_type_name(nametup+('iterator',))
    typeobj.rs_request_fn = _rs_name(nametup)
    typeobj.rs_checked_fn = _rs_name(nametup+('checked',))
    typeobj.rs_unchecked_fn = _rs_name(nametup+('unchecked',))

    typeobj.rs_aux_fn = _rs_name(nametup+('aux',))
    typeobj.rs_aux_checked_fn = _rs_name(nametup+('aux', 'checked'))
    typeobj.rs_aux_unchecked_fn = _rs_name(nametup+('aux', 'unchecked'))
    typeobj.rs_reply_type = _rs_type_name(nametup + ('reply',))
    typeobj.rs_cookie_type = _rs_type_name(nametup + ('cookie',))

    if typeobj.is_container:
        for field in typeobj.fields:
            _rs_type_setup(field.type, field.field_type)
            if field.type.is_list:
                _rs_type_setup(field.type.member, field.field_type)
            field.rs_field_name = _symbol(field.field_name)
            field.rs_field_type = _rs_type_name(field.field_type)

            field.rs_iterator_type = _rs_type_name(
                    field.field_type + ('iterator',))



def _rs_struct(typeobj):
    lifetime1 = "<'a>" if typeobj.has_lifetime else ""
    lifetime2 = "'a, " if typeobj.has_lifetime else ""

    _r.section(1)
    _r('')
    _r('pub struct %s%s {', typeobj.rs_type, lifetime1)
    _r('    pub base: %s<%s%s>', typeobj.rs_wrap_type,
                lifetime2, typeobj.ffi_type)
    _r('}')


def _rs_accessors(typeobj):
    lifetime = "<'a>" if typeobj.has_lifetime else ""

    _r.section(1)
    _r('')
    _r('impl%s %s%s {', lifetime, typeobj.rs_type, lifetime)
    with _r.indent_block():
        for (i, field) in enumerate(typeobj.fields):
            if field.visible:
                _rs_accessor(typeobj, field)
    _r('}')


def _rs_reply_accessors(reply):
    '''
    same as _rs_accessors but handles fds special case
    '''
    lifetime = "<'a>" if reply.has_lifetime else ""

    fd_field = None
    nfd_field = None
    for f in reply.fields:
        if f.rs_field_name == 'nfd':
            nfd_field = f
        if f.isfd:
            fd_field = f

    reply_fields = []
    for f in reply.fields:
        if f.rs_field_name == 'nfd':
            # writing nfd field only if fds is not written
            if not fd_field or not nfd_field:
                reply_fields.append(f)
        elif not f.isfd:
            reply_fields.append(f)


    _r.section(1)
    _r('')
    _r('impl%s %s%s {', lifetime, reply.rs_type, lifetime)
    with _r.indent_block():
        # regular fields
        for field in reply_fields:
            if field.visible:
                _rs_accessor(reply, field)

        # fds field if any
        if nfd_field and fd_field:
            getter = reply.request.ffi_reply_fds_fn
            # adding 's'
            fname = fd_field.rs_field_name
            if not fname.endswith('s'):
                fname += 's'
            _r('pub fn %s(&self, c: &base::Connection) -> &[i32] {', fname)
            with _r.indent_block():
                _r('unsafe {')
                with _r.indent_block():
                    _r('let nfd = (*self.base.ptr).nfd as usize;')
                    _r('let ptr = %s(c.get_raw_conn(), self.base.ptr);', getter)
                    _r('')
                    _r('std::slice::from_raw_parts(ptr, nfd)')
                _r('}')
            _r('}')
    _r('}')


def _rs_accessor(typeobj, field):
    if field.type.is_simple or field.type.is_union:
        _r('pub fn %s(&self) -> %s {', field.rs_field_name,
                field.rs_field_type)
        with _r.indent_block():
            _r('unsafe {')
            with _r.indent_block():
                _r('(*self.base.ptr).%s', field.ffi_field_name)
            _r('}')
        _r('}')

    elif field.type.is_list and not field.type.fixed_size():
        if field.type.member.is_simple:
            field_type = field.type.member.rs_type
            if field_type == 'c_char':
                return_type = '&str'
            else:
                return_type = '&[%s]' % field_type
            _r('pub fn %s(&self) -> %s {', field.rs_field_name, return_type)
            with _r.indent_block():
                _r('unsafe {')
                with _r.indent_block():
                    _r('let field = self.base.ptr;')
                    _r('let len = %s(field);', field.ffi_length_fn)
                    _r('let data = %s(field);', field.ffi_accessor_fn)
                    if field_type == 'c_char':
                        _r('let slice = ' +
                            'std::slice::from_raw_parts(' +
                                'data as *const u8, ' +
                                'len as usize);')
                        _r('// should we check what comes from X?')
                        _r('std::str::from_utf8_unchecked(&slice)')
                    else:
                        _r('std::slice::from_raw_parts(data, len as usize)')
                _r('}')
            _r('}')
        else:
            _r('pub fn %s(&self) -> %s {',
                    field.rs_field_name, field.rs_iterator_type)
            with _r.indent_block():
                _r('unsafe {')
                with _r.indent_block():
                    _r('%s(self.base.ptr)', field.ffi_iterator_fn)
                _r('}')
            _r('}')
            pass

    elif field.type.is_list:
        _r('pub fn %s(&self) -> &[%s] {',
                field.rs_field_name, field.rs_field_type)
        with _r.indent_block():
            _r('unsafe {')
            with _r.indent_block():
                _r('&(*self.base.ptr).%s', field.ffi_field_name)
            _r('}')
        _r('}')

    elif field.type.is_container:
        _r('pub fn %s(&self) -> %s {',
                field.rs_field_name, field.rs_field_type)
        with _r.indent_block():
            _r('unsafe {')
            with _r.indent_block():
                _r('std::mem::transmute(&(*self.base.ptr).%s)',
                        field.ffi_field_name)
            _r('}')
        _r('}')

    elif not field.type.is_pad:
        raise Exception('did not treat accessor %s.%s'
                % (typeobj.ffi_type, field.ffi_field_name))



def _rs_iterator(typeobj):

    lifetime1 = "<'a>" if typeobj.has_lifetime else ""
    lifetime2 = "'a, " if typeobj.has_lifetime else ""

    _r.section(1)
    _r('')
    _r("pub type %s%s = %s%s;",
            typeobj.rs_iterator_type, lifetime1, typeobj.ffi_iterator_type, lifetime1)

    _r('')
    _r("impl%s Iterator for %s%s {", lifetime1, typeobj.rs_iterator_type, lifetime1)
    _r("    type Item = %s%s;", typeobj.rs_type, lifetime1)
    _r("    fn next(&mut self) -> Option<%s%s> {", typeobj.rs_type, lifetime1)
    _r('        if self.rem == 0 { None }')
    _r('        else {')
    _r('            unsafe {')
    _r('                let iter = self as *mut %s;',
            typeobj.ffi_iterator_type)
    _r('                let data = (*iter).data;')
    _r('                %s(iter);', typeobj.ffi_next_fn)
    _r('                Some(std::mem::transmute(data))')
    _r('            }')
    _r('        }')
    _r('    }')
    _r('}')



def _rs_reply(request):

    _r.section(1)
    _r('')
    _r("pub struct %s {", request.rs_reply_type)
    _r("    base: base::Reply<%s>", request.ffi_reply_type)
    _r("}")
    _r("")
    _r("fn mk_reply_%s(reply: *mut %s)",
            request.ffi_reply_type, request.ffi_reply_type)
    _r("        -> %s {", request.rs_reply_type)
    _r("    %s {", request.rs_reply_type)
    _r("        base: base::mk_reply(reply)")
    _r("    }")
    _r("}")




# Common codegen utilities

class EnumCodegen(object):

    namecount = {}

    def build_collision_table(module):
        for v in module.types.values():
            key = _ffi_type_name(v[0])
            EnumCodegen.namecount[key] = (
                (EnumCodegen.namecount.get(key) or 0) + 1
            )


    def __init__(self, nametup):
        self._nametup = nametup

        self.done_vals = {}
        self.unique_discriminants = []
        self.conflicts = []
        self.all_discriminants = []
        key = _ffi_type_name(nametup)
        if EnumCodegen.namecount[key] > 1:
            nametup = nametup + ('enum',)
        self.ffi_name = _ffi_type_name(nametup)
        self.rs_name = _rs_type_name(nametup)


    def add_discriminant(self, name, val):
        class Discriminant: pass
        d = Discriminant()
        #d.rs_name = name
        d.rs_name = _tit_split(name).upper()
        if d.rs_name[0].isdigit():
            d.rs_name = '_' + d.rs_name
        d.ffi_name = _upper_name(_ext_nametup(self._nametup+(name,)))
        d.valstr = '0x%02x' % val
        d.val = val
        self.all_discriminants.append(d)
        if val in self.done_vals:
            self.conflicts.append(d)
        else:
            self.done_vals[val] = d
            self.unique_discriminants.append(d)


    def maxlen(self, name_field):
        maxnamelen = 0
        maxvallen = 0
        for d in self.unique_discriminants:
            maxvallen = max(maxvallen, len(d.valstr))
            maxnamelen = max(maxnamelen, len(getattr(d, name_field)))
        return (maxnamelen, maxvallen)


    def write_ffi(self):
        (maxnamelen, maxvallen) = self.maxlen('ffi_name')
        type_name = self.ffi_name
        _f.section(0)
        _f('')
        _f('pub type %s = u32;', type_name)
        for d in self.all_discriminants:
            d_name = d.ffi_name
            namespace = ' ' * (maxnamelen-len(d_name))
            valspace = ' ' * (maxvallen-len(d.valstr))
            _f('pub const %s%s: %s =%s %s;', d_name, namespace, type_name,
                    valspace, d.valstr)

    def write_rs(self):
        (maxnamelen, maxvallen) = self.maxlen("rs_name")
        _r.section(0)
        _r('')
        _r('pub mod %s {', self.rs_name)
        with _r.indent_block():
            for d in self.all_discriminants:
                namespace = ' ' * (maxnamelen-len(d.rs_name))
                valspace = ' ' * (maxvallen-len(d.valstr))
                _r('pub const %s%s: u32 =%s %s;', d.rs_name, namespace,
                        valspace, d.valstr)

        _r('}')




class RequestCodegen(object):

    def __init__(self, request):
        self.request = request

        self.void = False if self.request.reply else True

        self.ffi_cookie_type = ('xcb_void_cookie_t' if self.void
                else self.request.ffi_cookie_type)
        self.rs_cookie_type = ('base::VoidCookie' if self.void
                else self.request.rs_cookie_type)

        self.visible_fields = []
        for field in self.request.fields:
            if field.visible:
                self.visible_fields.append(field)

        # for, we do not filter out any visible field,
        # but we must find out if it is pointer, const ...
        self.ffi_params = []
        for field in self.visible_fields:
            ffi_rq_type = field.ffi_field_type
            if field.ffi_need_pointer:
                pointer = '*const ' if field.ffi_need_const else '*mut '
                ffi_rq_type = pointer + ffi_rq_type

            field.ffi_rq_type = ffi_rq_type
            self.ffi_params.append(field)


        # Rust is more complicated because of lists
        # here we pack lists in slices

        # there's basically 3 cases:
        # 1. regular fields, passed as-is to the ffi func
        # 2. masked lists (such as create_window event mask)
        #    given to rs slice of tuple (mask, value) and unpacked
        #    into int and pointer to ffi func
        # 3. regular lists, for which a length and a pointer
        #    must be passed to the ffi_func. these are given to
        #    rs by a slice

        # it happens to have 2 or more lists for same length field.
        # in this case, we will make 2 slices and runtime assert same length
        # eg: take a look at render::create_conical_gradient

        for f in self.visible_fields:
            f.rs_is_slice = False
            f.rs_lenfield = None
            f.rs_is_mask_slice = False
            f.rs_maskfield = None
            f.rs_skip = False

        for (ffi_index, field) in enumerate(self.visible_fields):
            field.ffi_index = ffi_index

            if field.type.is_list:

                if field.type.expr.bitfield:
                    # field associated with a mask
                    # eg. create_window last field
                    field.rs_is_mask_slice = True
                else:
                    # regular list with length and ptr
                    field.rs_is_slice = True
                field.rs_lenfield = field.type.expr.lenfield
                if not field.rs_lenfield:
                    len_name = field.type.expr.lenfield_name
                    for f in self.visible_fields:
                        if f.field_name == len_name:
                            field.rs_lenfield = f
                # the mask is mandatory, but not the length (eg c strings)
                if field.rs_is_mask_slice:
                    assert field.rs_lenfield
                if field.rs_lenfield:
                    field.rs_lenfield.rs_skip = True

        self.rs_params = []

        for field in self.visible_fields:
            if not field.rs_skip:
                self.rs_params.append(field)


    def ffi_func_name(self, regular, aux):
        checked = self.void and not regular
        unchecked = not self.void and not regular

        if checked:
            func_name = (self.request.ffi_checked_fn if not aux else
                    self.request.ffi_aux_checked_fn)
        elif unchecked:
            func_name = (self.request.ffi_unchecked_fn if not aux else
                    self.request.ffi_aux_unchecked_fn)
        else:
            func_name = (self.request.ffi_request_fn if not aux else
                    self.request.ffi_aux_fn)

        return func_name



    def rs_func_name(self, regular, aux):
        checked = self.void and not regular
        unchecked = not self.void and not regular

        if checked:
            func_name = (self.request.rs_checked_fn if not aux else
                    self.request.rs_aux_checked_fn)
        elif unchecked:
            func_name = (self.request.rs_unchecked_fn if not aux else
                    self.request.rs_aux_unchecked_fn)
        else:
            func_name = (self.request.rs_request_fn if not aux else
                    self.request.rs_aux_fn)

        return func_name



    def write_ffi_rs(self, regular, aux=False):
        self.write_ffi(regular, aux)
        self.write_rs(regular, aux)


    def write_ffi(self, regular, aux=False):

        ffi_func_name = self.ffi_func_name(regular, aux)

        # last tweak in field types
        for field in self.ffi_params:
            if field.type.ffi_need_serialize and not aux:
                field.ffi_rq_type = '*const c_void'


        maxnamelen = 1
        for p in self.ffi_params:
            maxnamelen = max(maxnamelen, len(p.ffi_field_name))

        _f.section(1)
        _f("")
        fn_start = "pub fn %s (" % ffi_func_name
        func_spacing = ' ' * len(fn_start)
        spacing = " " * (maxnamelen-len('c'))
        eol = ',' if len(self.ffi_params) else ')'
        _f("%sc: %s*mut xcb_connection_t%s", fn_start, spacing, eol)

        for (i, p) in enumerate(self.ffi_params):
            spacing = ' '*(maxnamelen-len(p.ffi_field_name))
            eol = ')' if i == (len(self.ffi_params)-1) else ','
            _f('%s%s: %s%s%s', func_spacing, p.ffi_field_name, spacing,
                    p.ffi_rq_type, eol)

        _f("        -> %s;", self.ffi_cookie_type)


    def write_rs(self, regular, aux=False):
        checked = self.void and not regular
        rs_func_name = self.rs_func_name(regular, aux)
        ffi_func_name = self.ffi_func_name(regular, aux)

        # last tweak in field types
        for field in self.ffi_params:
            if field.type.ffi_need_serialize and not aux:
                field.ffi_rq_type = '*const c_void'


        maxnamelen = len('c')
        for p in self.rs_params:
            maxnamelen = max(maxnamelen, len(p.rs_field_name))

        let_lines = []
        call_params = []

        _r.section(1)
        _r('')
        fn_start = "pub fn %s(" % rs_func_name
        func_spacing = ' ' * len(fn_start)
        eol = ',' if len(self.rs_params) else ')'
        spacing = ' ' * (maxnamelen-len('c'))
        _r("%sc%s: &base::Connection%s", fn_start, spacing, eol)

        for (i, p) in enumerate(self.rs_params):

            rs_typestr = p.rs_field_type

            if p.rs_is_mask_slice:

                maskfield = p.rs_lenfield
                rs_typestr = '&[(%s, %s)]' % (maskfield.rs_field_type,
                    p.rs_field_type)

                let_lines.append('let mut %s_copy = %s.to_vec();' %
                        (p.rs_field_name, p.rs_field_name))
                let_lines.append(('let (%s_mask, %s_vec) = ' +
                        'base::pack_bitfield(&mut %s_copy);') %
                        (p.rs_field_name, p.rs_field_name, p.rs_field_name))
                let_lines.append("let %s_ptr = %s_vec.as_ptr();" %
                        (p.rs_field_name, p.rs_field_name))

                # adding mask field if not already done
                # (already done should not happen with masks)
                if not next((cp for cp in call_params
                            if cp[0] == maskfield.ffi_index), None):
                    call_params.append((maskfield.ffi_index, "%s_mask as %s" %
                        (p.rs_field_name, maskfield.ffi_field_type)))

                # adding actual field
                call_params.append((p.ffi_index, '%s_ptr as %s' %
                        (p.rs_field_name, p.ffi_rq_type)))

            elif p.rs_is_slice:

                if field.type.member.rs_type == 'c_char':
                    rs_typestr = '&str'
                    let_lines.append('let %s = %s.as_bytes();' %
                            (p.rs_field_name, p.rs_field_name))
                elif field.type.member.rs_type == 'c_void':
                    rs_typestr = '&[u8]'
                else:
                    rs_typestr = '&[%s]' % rs_typestr

                if p.rs_lenfield:
                    lenfield = p.rs_lenfield
                    # adding mask field if not already done
                    # (already done should not happen with masks)
                    if not next((cp for cp in call_params
                            if cp[0] == lenfield.ffi_index), None):
                        let_lines.append('let %s_len = %s.len();' %
                                (p.rs_field_name, p.rs_field_name))
                        call_params.append((lenfield.ffi_index,
                                "%s_len as %s" %
                                (p.rs_field_name, lenfield.ffi_field_type)))

                let_lines.append('let %s_ptr = %s.as_ptr();' %
                        (p.rs_field_name, p.rs_field_name))
                # adding actual field
                call_params.append((p.ffi_index, '%s_ptr as %s' %
                        (p.rs_field_name, p.ffi_rq_type)))

            elif p.type.is_container:
                call_params.append((p.ffi_index, '*(%s.base.ptr)' %
                        p.rs_field_name))

            else:
                call_params.append((p.ffi_index,
                        '%s as %s' % (p.rs_field_name, p.ffi_rq_type)))

            spacing = ' ' * (maxnamelen-len(p.rs_field_name))
            eol = ',' if i < (len(self.rs_params)-1) else ')'
            _r('%s%s%s: %s%s', func_spacing, p.rs_field_name,
                    spacing, rs_typestr, eol)

        _r("        -> %s {", self.rs_cookie_type)

        with _r.indent_block():
            _r('unsafe {')
            with _r.indent_block():
                for l in let_lines:
                    _r(l)

                call_start = 'let cookie = %s(' % ffi_func_name
                eol = ',' if len(call_params) else ');'
                spacing = ' ' * len(call_start)
                _r('%sc.get_raw_conn()%s', call_start, eol)

                call_params.sort(key=lambda x: x[0])

                for (i, (ffi_ind, p)) in enumerate(call_params):
                    eol = ',' if i < (len(call_params)-1) else ');'
                    _r('%s%s%s  // %d', spacing, p, eol, ffi_ind)

                _r("%s {", self.rs_cookie_type)
                _r("    base: base::Cookie { ")
                _r("        cookie:  cookie,")
                _r("        conn:    c.get_raw_conn(),")
                _r("        checked: %s", 'true' if checked else 'false')
                _r("    }")
                _r("}")
            _r('}')
        _r('}')




def _opcode(nametup, opcode):
    ffi_name = _upper_name(_ext_nametup(nametup))
    _f.section(0)
    _f('')
    _f('pub const %s: u32 = %s;', ffi_name, opcode)

    rs_name = _upper_name(_rs_extract_module(nametup)[1])
    _r.section(0)
    _r('')
    _r('pub const %s: u32 = %s;', rs_name, opcode)



def _cookie(request):
    _f.section(0)
    _f('')
    _f('#[derive(Copy, Clone)]')
    _f('#[repr(C)]')
    _f('pub struct %s {', request.ffi_cookie_type)
    _f('    sequence: c_uint')
    _f('}')

    _r.section(0)
    _r("")
    _r("pub struct %s {", request.rs_cookie_type)
    _r("    pub base: base::Cookie<%s>", request.ffi_cookie_type)
    _r("}")

    cookie = request.rs_cookie_type
    mk_func = 'mk_reply_%s' % request.ffi_reply_type
    reply = request.rs_reply_type
    func = request.ffi_reply_fn
    funcspace = ' ' * len(func)

    _r.section(1)
    _r('')
    _r("impl base::ReplyCookie<%s> for %s {", reply, cookie)
    with _r.indent_block():
        _r("fn get_reply(&self) -> Result<%s, base::GenericError> {", reply)
        with _r.indent_block():
            _r('unsafe {')
            with _r.indent_block():
                _r("let mut err: *mut xcb_generic_error_t = std::ptr::null_mut();")
                _r('let reply = if self.base.checked {')
                _r('    %s(self.base.conn,', func)
                _r('    %s self.base.cookie,', funcspace)
                _r('    %s &mut err)', funcspace)
                _r('} else {')
                _r('    %s(self.base.conn,', func)
                _r('    %s self.base.cookie,', funcspace)
                _r('    %s std::ptr::null_mut())', funcspace)
                _r('};')
                _r('if err.is_null() {')
                _r('    Ok(%s(reply))', mk_func)
                _r('} else {')
                _r('    libc::free(reply as *mut c_void);')
                _r('    Err(base::GenericError { base: base::mk_error(err) } )')
                _r('}')
            _r('}')
        _r('}')
    _r('}')




def _must_pack_event(event, nametup):
    # The generic event structure xcb_ge_event_t has the full_sequence field
    # at the 32byte boundary. That's why we've to inject this field into GE
    # events while generating the structure for them. Otherwise we would read
    # garbage (the internal full_sequence) when accessing normal event fields
    # there.
    must_pack = False
    if (hasattr(event, 'is_ge_event')
            and event.is_ge_event
            and event.name == nametup):
        event_size = 0
        for field in event.fields:
            if field.type.size != None and field.type.nmemb != None:
                event_size += field.type.size * field.type.nmemb
            if event_size == 32:
                full_sequence = Field(tcard32,
                        tcard32.name, 'full_sequence',
                        False, True, True)
                idx = event.fields.index(field)
                event.fields.insert(idx + 1, full_sequence)

                # If the event contains any 64-bit extended fields, they need
                # to remain aligned on a 64-bit boundary. Adding full_sequence
                # would normally break that; force the struct to be packed.
                must_pack = any(f.type.size == 8 and f.type.is_simple
                        for f in event.fields[(idx+1):])
                break

    return must_pack



# codegen drivers

def rs_simple(simple, nametup):
    '''
    simple is SimpleType object
    nametup is a name tuple
    '''
    current_handler = ('simple:  ', nametup)

    simple.has_lifetime = False

    _ffi_type_setup(simple, nametup)
    _f.section(0)
    assert len(simple.name) == 1
    _f('')
    _f('pub type %s = %s;', simple.ffi_type, simple.name[0])
    _ffi_iterator(simple, nametup)

    _rs_type_setup(simple, nametup)
    _r.section(0)
    _r('')
    _r('pub type %s = %s;', simple.rs_type, simple.ffi_type)



def rs_enum(typeobj, nametup):
    '''
    typeobj is xcbgen.xtypes.Enum object
    nametup is a name tuple
    '''
    current_handler = ('enum:    ', nametup)

    ecg = EnumCodegen(nametup)

    val = -1
    for (enam, eval) in typeobj.values:
        val = int(eval) if eval != '' else val+1
        ecg.add_discriminant(enam, val)

    ecg.write_ffi()
    ecg.write_rs()




def rs_struct(struct, nametup):
    '''
    struct is Struct object
    nametup is a name tuple
    '''
    current_handler = ('struct:  ', nametup)

    struct.rs_wrap_type = 'base::StructPtr'
    struct.has_lifetime = True

    _ffi_type_setup(struct, nametup)
    _ffi_struct(struct)
    _ffi_accessors(struct, nametup)
    _ffi_iterator(struct, nametup)

    _rs_type_setup(struct, nametup)
    _rs_struct(struct)
    _rs_accessors(struct)
    _rs_iterator(struct)



def rs_union(union, nametup):
    '''
    union is Union object
    nametup is a name tuple
    '''
    current_handler = ('union:   ', nametup)

    union.has_lifetime = False

    _ffi_type_setup(union, nametup)

    biggest = 1
    most_aligned = 1
    ptr_size = 8 if sys.maxsize > 2**32 else 4
    for field in union.fields:
        fs = ptr_size
        fa = ptr_size
        if field.type.size:
            fs = field.type.size
            fa = field.type.size
        if field.type.nmemb:
            fs = fa * field.type.nmemb
        biggest = max(biggest, fs)
        most_aligned = max(most_aligned, fa)

    assert biggest >= most_aligned

    num_aligned = int(biggest / most_aligned)
    if biggest % most_aligned:
        num_aligned += 1

    num_bytes = num_aligned * most_aligned

    _f.section(0)
    _f('')
    _f('#[repr(C)]')
    _f('pub struct %s {', union.ffi_type)
    _f('    pub data: [u8; %s]', num_bytes)
    _f('}')

    _f('')
    _f('impl Copy for %s {}', union.ffi_type)
    _f('impl Clone for %s {', union.ffi_type)
    _f('    fn clone(&self) -> %s { *self }', union.ffi_type)
    _f('}')

    _rs_type_setup(union, nametup)
    _r.section(0)
    _r('')
    _r('pub type %s = %s;', union.rs_type, union.ffi_type)



def rs_request(request, nametup):
    '''
    request is Request object
    nametup is a name tuple
    '''
    current_handler = ('request: ', nametup)

    request.rs_wrap_type = 'StructPtr'
    request.has_lifetime = False

    _ffi_type_setup(request, nametup, ('request',))
    _rs_type_setup(request, nametup, ('request',))

    rcg = RequestCodegen(request)

    _opcode(nametup, request.opcode)
    _ffi_struct(request)

    if request.reply:
        # enable getting the request from the reply
        request.reply.request = request
        request.reply.rs_wrap_type = 'base::Reply'
        request.reply.has_lifetime = False

        _cookie(request)

        _ffi_type_setup(request.reply, nametup, ('reply',))
        _ffi_struct(request.reply)
        _ffi_accessors(request.reply, nametup + ('reply',))
        _ffi_reply(request)
        if _ffi_reply_has_fds(request.reply):
            _ffi_reply_fds(request, nametup)

        _rs_type_setup(request.reply, nametup, ('reply',))
        _rs_reply(request)
        _rs_reply_accessors(request.reply)

    # regular call 'request_name'
    rcg.write_ffi_rs(True, False)
    # unregular call 'request_name_checked' or 'request_name_unchecked'
    # depending on cookie type
    rcg.write_ffi_rs(False, False)

    if request.ffi_need_aux:
        rcg.write_ffi_rs(True, True)
        rcg.write_ffi_rs(False, True)


def rs_event(event, nametup):
    '''
    event is Event object
    nametup is a name tuple
    '''
    current_handler = ('event:   ', nametup)

    must_pack = _must_pack_event(event, nametup)

    if must_pack:
        print('event ', nametup, ' is packed')

    for f in event.fields:
        if f.field_name.lower() == 'new':
            f.field_name = 'new_'

    _ffi_type_setup(event, nametup, ('event',))
    _opcode(nametup, event.opcodes[nametup])

    _rs_type_setup(event, nametup, ('event',))

    _r.section(0)
    _r('')
    _r('pub struct %s {', event.rs_type)
    _r('    pub base: base::Event<%s>', event.ffi_type)
    _r('}')

    if event.name == nametup:
        _ffi_struct(event, must_pack)

        accessor_fields = []
        for f in event.fields:
            if not f.visible: continue
            accessor_fields.append(f)
            if f.type.is_list or f.type.is_switch or f.type.is_bitcase:
                try:
                    accessor_fields.remove(f.type.expr.lenfield)
                except:
                    pass

        new_params = []

        _r.section(1)
        _r('')
        _r('impl %s {', event.rs_type)
        with _r.indent_block():
            for f in accessor_fields:
                _rs_accessor(event, f)

                rs_ftype = f.rs_field_type
                if f.has_subscript:
                    rs_ftype = "[%s; %d]" % (rs_ftype, f.type.nmemb)

                new_params.append("%s: %s" % (f.rs_field_name, rs_ftype))

            fn_start = "pub fn new("
            fn_space = ' ' * len(fn_start)
            p = new_params[0] if len(new_params) else ''
            eol = ',' if len(new_params)>1 else ')'
            _r('%s%s%s', fn_start, p, eol)
            for (i, p) in enumerate(new_params[1:]):
                eol = ',' if i != len(new_params)-2 else ')'
                _r("%s%s%s", fn_space, p, eol)

            _r('        -> %s {', event.rs_type)
            with _r.indent_block():
                _r('unsafe {')
                with _r.indent_block():
                    _r('let raw = libc::malloc(32 as usize) as *mut %s;', event.ffi_type)
                    for f in event.fields:
                        if not f.visible: continue
                        if f.type.is_container and not f.type.is_union:
                            _r('(*raw).%s = *%s.base.ptr;',
                                    f.ffi_field_name, f.rs_field_name)
                        else:
                            _r('(*raw).%s = %s;',
                                    f.ffi_field_name, f.rs_field_name)
                    _r('%s {', event.rs_type)
                    _r('    base: base::Event {')
                    _r('        ptr: raw')
                    _r('    }')
                    _r('}')
                _r('}')
            _r('}')
        _r('}')


    else:
        _f.section(0)
        _f('')
        _f('pub type %s = %s;', _ffi_type_name(nametup+('event',)),
                            _ffi_type_name(event.name+('event',)))



def rs_error(error, nametup):
    '''
    error is Error object
    nametup is a name tuple
    '''
    current_handler = ('error:   ', nametup)

    _ffi_type_setup(error, nametup, ('error',))
    _opcode(nametup, error.opcodes[nametup])

    if error.name == nametup:
        _ffi_struct(error)
    else:
        _f.section(0)
        _f('')
        _f('pub type %s = %s;', _ffi_type_name(nametup+('error',)),
                            _ffi_type_name(error.name+('error',)))

    _rs_type_setup(error, nametup, ('error',))
    _r.section(0)
    _r('')
    _r('pub struct %s {', error.rs_type)
    _r('    pub base: base::Error<%s>', error.ffi_type)
    _r('}')


def usage(program):
    print('Usage: {} -o SRCDIR file.xml', program, file=sys.stderr)


if __name__ == '__main__':

    from optparse import OptionParser

    parser = OptionParser(usage="Usage: %prog -o SRCDIR file.xml")
    parser.add_option('-o', '--output', dest='srcdir', metavar='SRCDIR',
                help='specifies rust src dir where to generate files')

    (options, args) = parser.parse_args(sys.argv)

    if options.srcdir == None:
        parser.error('-o SRCDIR is mandatory')

    if not os.path.isdir(options.srcdir):
        parser.error('-o SRCDIR must be a directory')

    if len(args) < 2:
        parser.error('input XML file must be supplied')

    output = {  'open'      : rs_open,
                'close'     : rs_close,
                'simple'    : rs_simple,
                'enum'      : rs_enum,
                'struct'    : rs_struct,
                'union'     : rs_union,
                'request'   : rs_request,
                'event'     : rs_event,
                'error'     : rs_error }
    try:
        from xcbgen.state import Module
        from xcbgen.xtypes import *
    except ImportError:
        print('failed to load xcbgen', file=sys.stderr)
        raise

    # Parse the xml header
    module = Module(args[1], output)
    module.rs_srcdir = options.srcdir

    # Build type-registry and resolve type dependencies
    module.register()
    module.resolve()

    # Output the code
    try:
        module.generate()
    except:
        print('error occured in handler: ', current_handler, file=sys.stderr)
        raise