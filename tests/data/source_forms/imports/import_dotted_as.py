"""Review R13: import a.b as c binds c to a.b; import a.b.c binds a."""

import os.path as osp
import xml.dom
import xml.etree.ElementTree


def f():
    return osp.join, xml.etree.ElementTree.parse, xml.dom.minidom
