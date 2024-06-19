import argparse
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

escape_illegal_xml_characters = lambda x: re.sub(u'[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]', '', x)


def get_documents(data):
    """Parse Docushare XML"""
    root = ET.fromstring(data)

    collections = {}
    documents = {}
    users = {}

    for child in root:
        if 'classname' not in child.attrib:
            continue

        id_ = child.attrib['handle']
        type_ = child.attrib['classname']

        if type_ in ('Document', 'Collection', 'URL'):
            owner = None
            for obj in child.findall('./destinationlinks/owner'):
                owner = obj.text

            private = True
            acls = child.find('acls')
            for acl in acls:
                if acl.attrib['principal'] == 'Group-5':
                    if 'readobject' in acl.attrib['permissions']:
                        private = False
            
        if type_ == 'Document':
            props = child.find('props')
            if not props:
                title = id_
            else:
                for prop in props:
                    if prop.attrib['name'] == 'title':
                        title = prop.text
                        break
                else:
                    print(ET.tostring(child).decode('utf8'))
                    raise Exception('no title in document')

            versions = child.find('versions')
            if versions is None:
                print(ET.tostring(child).decode('utf8'))
                raise Exception('no versions')
            elif len(versions) == 1:
                version_object = versions.findall('dsobject')[0]
            else:
                for obj in child.findall('./destinationlinks/preferredVersion'):
                    # we have a preferred version, so use it
                    handle = obj.text
                    #print("preferred version handle", handle)
                    for v in versions.findall('dsobject'):
                        if v.attrib['handle'] == handle:
                            version_object = v
                            break
                    else:
                        raise Exception('no matching version')
                    break
                else:
                    print(ET.tostring(child))
                    raise Exception('no preferredVersion')

            renditions = version_object.findall('./renditions/dsobject')
            if len(renditions) == 0:
                print(ET.tostring(child))
                raise Exception('no rendition')
            elif len(renditions) > 1:
                print(ET.tostring(child))
                raise Exception('too many renditions')
            for r in renditions:
                for prop in r.find('props'):
                    if prop.attrib['name'] == 'size':
                        size = prop.text
                        break
                else:
                    size = -1
                for o in r.findall('./contentelements/contentelement'):
                    filename = o.attrib['filename']
                    if title == id_:
                        title = o.text
                    break
                else:
                    filename = None
                break
            else:
                size = -1
                filename = None

            documents[id_] = {
                'type': 'Document',
                'title': title,
                'size': size,
                'filename': filename,
                'owner': owner,
                'private': private,
            }

        elif type_ == 'Collection':
            title = ''
            sort_order = 'Title'
            props = child.find('props')
            if not props:
                title = id_
            else:
                for prop in props:
                    if prop.attrib['name'] == 'title':
                        title = prop.text
                    if prop.attrib['name'] == 'sort_order':
                        sort_order = prop.text

            documents[id_] = {
                'type': 'Collection',
                'title': title,
                'sort_order': sort_order,
                'owner': owner,
                'private': private,
                'children': [e.text for e in child.findall('./destinationlinks/containment')],
            }

        elif type_ == 'URL':
            title = ''
            url = ''
            props = child.find('props')
            if not props:
                title = id_
            else:
                for prop in props:
                    if prop.attrib['name'] == 'title':
                        title = prop.text
                    if prop.attrib['name'] == 'url':
                        url = prop.text

            if not url:
                continue

            documents[id_] = {
                'type': 'URL',
                'title': title,
                'size': -1,
                'filename': '',
                'url': url,
                'owner': owner,
                'private': private,
            }

        elif type_ == 'User':
            props = child.find('props')
            if props is None:
                print(ET.tostring(child))
                raise Exception('no props')
            username = None
            for prop in props:
                if prop.attrib['name'] == 'username':
                    username = prop.text
                    break
            else:
                raise Exception('no username in user')

            documents[id_] = {
                'type': 'User',
                'username': username,
            }

        elif type_ == 'Group':
            continue

        else:
            print(ET.tostring(child).decode('utf8'))
            raise Exception('new type')
            

    return documents


class TreeNode(list):
    def __init__(self):
        super().__init__()
        self.parent = None


class Tree:
    def __init__(self, documents=None):
        self.nodes = {}
        self.roots = set()
        self.documents = documents

    def add_node(self, id_, parent=None, children=None):
        if id_ in self.nodes:
            node = self.nodes[id_]
        else:
            node = self.nodes[id_] = TreeNode()

        if parent:
            node.parent = parent
            self.nodes[parent].append(id_)
            self.roots.discard(id_)
        elif not node.parent:
            self.roots.add(id_)

        if children:
            self.add_children(id_, children)

    def add_children(self, id_, children):
        node = self.nodes[id_]
        if len(node) > 0:
            for c in children:
                if c not in node:
                    node.append(c)
        else:
            node.extend(children)

    def set_parent(self, parent_id, child_id):
        if child_id not in self.nodes:
            self.add_node(child_id, parent=parent_id)
        else:
            self.add_children(parent_id, [child_id])
            self.nodes[child_id].parent = parent_id
            self.roots.discard(child_id)


class TreeSorts:
    @staticmethod
    def lookup(name):
        try:
            return getattr(TreeSorts, name)
        except AttributeError:
            return TreeSorts.Default

    @staticmethod
    def Title(tree, id_):
        def key(k):
            try:
                return (0, tree.documents[k]['title'])
            except KeyError:
                return (1,'')
        tree.nodes[id_].sort(key=key)

    @staticmethod
    def TitleReversed(tree, id_):
        def key(k):
            try:
                return (0, tree.documents[k]['title'])
            except KeyError:
                return (1,'')
        tree.nodes[id_].sort(key=key, reverse=True)

    TypeAndTitle = Title
    TypeAndTitleReversed = TitleReversed

    @staticmethod
    def Default(tree, id_):
        pass


def build_tree(documents):
    tree = Tree(documents)
    for id_ in documents:
        doc = documents[id_]
        if doc['type'] == 'Collection':
            tree.add_node(id_, children=doc['children'])
            for child in doc['children']:
                if child.startswith('Collection'):
                    tree.set_parent(id_, child)
    for id_ in documents:
        doc = documents[id_]
        if doc['type'] == 'Collection' and doc['children']:
            TreeSorts.lookup(doc['sort_order'])(tree, id_)
    return tree


def walk_tree(tree, id_=None, level=0):
    if level >= 2:
        return

    if id_ is None:
        for r in tree.roots:
            yield from walk_tree(tree, r, level=0)
    else:
        yield id_, level
        for d in tree.nodes[id_]:
            if d in tree.documents:
                doc = tree.documents[d]
            else:
                doc = {'type': 'Document'}
            if doc['type'] == 'Collection':
                yield from walk_tree(tree, id_=d, level=level+1)
            else:
                yield d, level+1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_xml', nargs='+', help='use input xml path for testing')
    parser.add_argument('--output', help='output directory (specify to get output)')
    args = parser.parse_args()

    documents = {}
    if args.input_xml:
        for path in args.input_xml:
            with open(path, 'r') as f:
                input_xml = escape_illegal_xml_characters(f.read())
                documents.update(get_documents(input_xml))
                del input_xml
    else:
        path = '/root/docuhide/Collection/Collection.xml'
        if not os.path.exists(path):
            subprocess.check_call('./dsexport.sh -d /root/docuhide -r -m Collection', cwd='/root/docushare/bin', shell=True)
        with open('/root/docuhide/Collection/Collection.xml', 'r') as f:
            input_xml = escape_illegal_xml_characters(f.read())
            
        path = '/root/docuhide/Document/Document.xml'
        if not os.path.exists(path):
            subprocess.check_call('./dsexport.sh -d /root/docuhide -r -m Document', cwd='/root/docushare/bin', shell=True)
        with open('/root/docuhide/Document/Document.xml', 'r') as f:
            input_xml = escape_illegal_xml_characters(f.read())

    tree = build_tree(documents)

    # print tree
    skip = False
    for id_, level in walk_tree(tree):
        if skip and level > 0:
            continue
        try:
            doc = documents[id_]
        except KeyError:
            doc = {'type': 'Document', 'title': '', 'owner': '', 'private': False}
        if doc['type'] == 'Collection':
            title = doc['title']
            owner = doc['owner']
            private = doc['private']
        else:
            title = doc['title']
            owner = doc['owner']
            private = doc['private']
        if level == 0 and title.startswith('Personal '):
            skip = True
            continue
        skip = False
        user = documents.get(owner,{}).get('username','root')
        print('|'+' '*level + id_, title, user, private)

    return

    # get all doc details
    for id_ in list(documents.keys()):
        for child_id in tree.nodes[id_]:
            if child_id not in documents:
                path = '/root/docuhide/'+child_id+'/'+child_id+'.xml'
                if not os.path.exists(path):
                    subprocess.check_call('./dsexport.sh -d /root/docuhide -r -m '+child_id, cwd='/root/docushare/bin', shell=True)
                with open('/root/docuhide/'+child_id+'/'+child_id+'.xml', 'r') as f:
                    input_xml = escape_illegal_xml_characters(f.read())
                documents.update(get_documents(input_xml))
                del input_xml

if __name__ == '__main__':
    main()
