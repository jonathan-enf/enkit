# -*- coding: utf-8 -*-
"""Reformat markdown.

A simple wrapper for the mdformat library.
"""

# third party libraries
import mdformat


def format_string(unformatted):
    extensions = {"gfm", "tables"}
    options = {"wrap": 78}
    formatted = mdformat.text(unformatted, options=options, extensions=extensions)
    return formatted


def format_file(in_fname, out_fname, check=False):
    with open(in_fname, "r", encoding="utf-8") as fd:
        unformatted = fd.read()
    formatted = format_string(unformatted)
    if check:
        return formatted == unformatted
    else:
        with open(out_fname, "w", encoding="utf-8") as fd:
            fd.write(formatted)
        return True
