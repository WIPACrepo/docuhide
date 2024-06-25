from __future__ import print_function, unicode_literals, division

import argparse
from datetime import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET


escape_illegal_xml_characters = lambda x: re.sub(u'[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]', '', x)


CHMOD_OWNER_FILE = stat.S_IRUSR | stat.S_IWUSR
CHMOD_OWNER_DIR = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
CHMOD_ALL_FILE = CHMOD_OWNER_FILE | stat.S_IRGRP | stat.S_IROTH
CHMOD_ALL_DIR = CHMOD_OWNER_FILE | stat.S_IRGRP | stat.S_IROTH | stat.S_IXGRP | stat.S_IXOTH


def sanitize(name):
    # convert name to valid posix
    return name.replace('/', '-').replace(';', '-').replace('$', '-')


# load username to uid cache
UID_CACHE = {'root': 0, 'icecube': 0}
UID_CACHE_PATH = os.path.join(os.path.dirname(__file__), 'username_uid_map.json')
if os.path.exists(UID_CACHE_PATH):
    with open(UID_CACHE_PATH) as f:
        UID_CACHE.update(json.load(f))
else:
    print('Loading usernames from LDAP', file=sys.stderr, end='\n')
    from ldap3 import Connection, ALL
    conn = Connection('ldap-1.icecube.wisc.edu', auto_bind=True)
    entries = conn.extend.standard.paged_search('ou=People,dc=icecube,dc=wisc,dc=edu', '(objectclass=posixAccount)', attributes=['uid', 'uidNumber'], paged_size=100)
    for entry in entries:
        attrs = entry['attributes']
        UID_CACHE[attrs['uid'][0]] = attrs['uidNumber']
    with open(UID_CACHE_PATH, 'w') as f:
        json.dump(UID_CACHE, f)


def total_seconds(timedelta):
    # because python 2.6 is super old, we do this directly instead of .total_seconds()
    return int(timedelta.seconds + timedelta.days * 24 * 3600)


def get_documents(data, details=False):
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
                if acl.attrib['principal'] == 'Group-4':
                    if 'readobject' in acl.attrib['permissions']:
                        private = False
                        break
                if acl.attrib['principal'] == 'Group-5':
                    if 'readobject' in acl.attrib['permissions']:
                        private = False
                        break
                if acl.attrib['principal'] == 'Group-7':
                    if 'readobject' in acl.attrib['permissions']:
                        private = False
                        break
            
        if type_ == 'Document':
            if not details:
                documents[id_] = {
                    'type': 'Document',
                    'owner': owner,
                }
            else:
                props = child.find('props')
                title = id_
                if props is not None and len(props) > 0:
                    for prop in props:
                        if prop.attrib['name'] == 'title':
                            title = prop.text
                            break

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
                size = -1
                date = None
                for r in renditions:
                    for prop in r.find('props'):
                        if prop.attrib['name'] == 'size':
                            size = prop.text
                        if prop.attrib['name'] == 'create_date':
                            date = total_seconds(datetime.strptime(prop.text, '%a %b %d %H:%M:%S %Z %Y') - datetime.fromtimestamp(0))
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
                    'owner': owner,
                    'title': title,
                    'private': private,
                    'size': size,
                    'filename': filename,
                    'date': date,
                }

        elif type_ == 'Collection':
            title = ''
            sort_order = 'Title'
            date = None
            props = child.find('props')
            if props is None or len(props) == 0:
                title = id_
            else:
                for prop in props:
                    if prop.attrib['name'] == 'title':
                        title = prop.text
                    if prop.attrib['name'] == 'sort_order':
                        sort_order = prop.text
                    if prop.attrib['name'] == 'create_date':
                        date = total_seconds(datetime.strptime(prop.text, '%a %b %d %H:%M:%S %Z %Y') - datetime.fromtimestamp(0))

            documents[id_] = {
                'type': 'Collection',
                'title': title,
                'sort_order': sort_order,
                'date': date,
                'owner': owner,
                'private': private,
                'children': [e.text for e in child.findall('./destinationlinks/containment')],
            }

        elif type_ == 'URL':
            title = ''
            url = ''
            props = child.find('props')
            if props is None or len(props) == 0:
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

        elif type_ == 'BulletinBoard':
            continue

        else:
            print(ET.tostring(child).decode('utf8'))
            raise Exception('new type')

        child.clear()

    return documents


class TreeNode(list):
    def __init__(self):
        super(TreeNode, self).__init__()
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

        if parent is not None:
            node.parent = parent
            if id_ not in self.nodes[parent]:
                self.nodes[parent].append(id_)
            self.roots.discard(id_)
        elif node.parent is None:
            self.roots.add(id_)

        if children is not None:
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


def walk_tree(tree, id_=None, level=0, skip_level=None):
    if skip_level and level >= skip_level:
        return

    if id_ is None:
        for r in tree.roots:
            for ret in walk_tree(tree, r, level=0, skip_level=skip_level):
                yield ret
    else:
        yield id_, level
        for d in tree.nodes[id_]:
            if d in tree.documents:
                doc = tree.documents[d]
            else:
                doc = {'type': 'Document'}
            if doc['type'] == 'Collection':
                for ret in walk_tree(tree, id_=d, level=level+1, skip_level=skip_level):
                    yield ret
            else:
                yield d, level+1


DOCUHIDE_PATH = '/root/docuhide/'


def dsexport(arg, recursive=False, metadata=False, props=None):
    cmd = './dsexport.sh -d '+DOCUHIDE_PATH+' '
    if recursive:
        cmd += '-r -t 4 '
    if metadata:
        cmd += '-m '
    if props:
        cmd += '-p '+','.join(props)+' '
    cmd += arg
    FNULL = open('/root/docuhide/export_err', 'w')
    subprocess.check_call(cmd, cwd='/root/docushare/bin', shell=True, stdout=FNULL, stderr=subprocess.STDOUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_xml', nargs='+', help='use input xml path for testing')
    parser.add_argument('--output', help='output directory (specify to get output)')
    parser.add_argument('--max-depth', default=None, type=int, help='max depth of output tree')
    parser.add_argument('--sub-collection', default=None, help='sub-collection to run on')
    args = parser.parse_args()


    documents = {}
    if args.input_xml:
        for path in args.input_xml:
            with open(path, 'r') as f:
                input_xml = escape_illegal_xml_characters(f.read())
                documents.update(get_documents(input_xml, details=True))
                del input_xml
    else:
        path = DOCUHIDE_PATH+'Collection/Collection.xml'
        if not os.path.exists(path):
            print('Running dsexport for Collection metadata', file=sys.stderr, end='\n')
            dsexport('Collection', metadata=True, props=['title', 'create_date', 'sort_order'])
        print('Processing Collection metadata', file=sys.stderr, end='\n')
        with open(path, 'r') as f:
            input_xml = escape_illegal_xml_characters(f.read())
        documents.update(get_documents(input_xml))
        del input_xml

        path = DOCUHIDE_PATH+'Document/Document.xml'
        if not os.path.exists(path):
            print('Running dsexport for Document metadata', file=sys.stderr, end='\n')
            dsexport('Document', metadata=True, props=['noprops'])
        print('Processing Document metadata', file=sys.stderr, end='\n')
        with open(path, 'r') as f:
            input_xml = escape_illegal_xml_characters(f.read())
        documents.update(get_documents(input_xml))
        del input_xml

        path = DOCUHIDE_PATH+'URL/URL.xml'
        if not os.path.exists(path):
            print('Running dsexport for URL metadata', file=sys.stderr, end='\n')
            dsexport('URL', metadata=True, props=['noprops'])
        print('Processing URL metadata', file=sys.stderr, end='\n')
        with open(path, 'r') as f:
            input_xml = escape_illegal_xml_characters(f.read())
        documents.update(get_documents(input_xml))
        del input_xml

    print('Completed processing metadata. Building tree', file=sys.stderr, end='\n')

    tree = build_tree(documents)

    # print tree
    print('Outputting tree and documents', file=sys.stderr, end='\n')
    parent_paths = {}
    for id_, level in walk_tree(tree, id_=args.sub_collection, skip_level=args.max_depth):
        # print tree
        try:
            doc = documents[id_]
        except KeyError:
            doc = {'type': 'Document', 'title': '', 'owner': '', 'private': False}

        if doc['type'] == 'Collection':
            title = doc['title']
            owner = doc['owner']
            private = doc['private']
        else:
            owner = doc['owner']
        user = documents.get(owner,{}).get('username','root')
        uid = UID_CACHE[user]

        # make posix output
        if args.output:
            if level == 0:
                dir_path = args.output
            else:
                dir_path = parent_paths[level-1]

            #parent_path = []
            #parent_id = tree.nodes[id_].parent
            #while parent_id:
            #    parent_path.insert(0, sanitize(documents[parent_id]['title']))
            #    parent_id = tree.nodes[parent_id].parent
            #dir_path = os.path.join(args.output, *parent_path)
            #if not os.path.exists(dir_path):
            #    os.makedirs(dir_path)
            #parent_paths[level] = dir_path

            if doc['type'] == 'Collection':
                dest_path = os.path.join(dir_path, sanitize(doc['title']))
                parent_paths[level] = dest_path
                if not os.path.exists(dest_path):
                    os.mkdir(dest_path)
                if private:
                    perms = CHMOD_OWNER_DIR
                else:
                    perms = CHMOD_ALL_DIR
                if doc.get('date', None) is not None:
                    try:
                        os.utime(dest_path, (doc['date'], doc['date']))
                    except Exception:
                        print('cannot set time on', dest_path, file=sys.stderr)
            else:
                # we need to get the actual document
                path = os.path.join(DOCUHIDE_PATH, id_)
                dsexport(id_)
                with open(os.path.join(path, id_+'.xml'), 'r') as f:
                    input_xml = escape_illegal_xml_characters(f.read())
                old_doc = doc
                new_docs = get_documents(input_xml, details=True)
                doc = new_docs[id_]
                del input_xml
                title = doc['title']
                private = doc['private']

                if doc['type'] == 'URL':
                    dest_path = os.path.join(dir_path, sanitize(doc['title']))
                    with open(dest_path, 'w') as f:
                        print(doc['url'], file=f)
                elif doc['type'] == 'Document':
                    src_path = os.path.join(path, 'documents', doc['filename'])
                    dest_path = os.path.join(dir_path, sanitize(doc['title']))
                    ext = os.path.splitext(doc['filename'])[-1]
                    if ext:
                        dest_path += '.'+ext
                    try:
                        shutil.copyfile(src_path, dest_path)
                    except Exception:
                        print('old_doc', old_doc, file=sys.stderr)
                        print('doc', doc, file=sys.stderr)
                        print('src_path', src_path, file=sys.stderr)
                        print('dest_path', dest_path, file=sys.stderr)
                        raise
                elif doc['type'] == 'BulletinBoard':
                    continue
                else:
                    print('id:', id_, file=sys.stderr)
                    print('old_doc', old_doc, file=sys.stderr)
                    print('doc', doc, file=sys.stderr)
                    raise Exception('unknown doc type')

                if doc.get('date', None) is not None:
                    try:
                        os.utime(dest_path, (doc['date'], doc['date']))
                    except Exception:
                        print('cannot set time on', dest_path, file=sys.stderr)

                # clean up
                shutil.rmtree(path)

                if private:
                    perms = CHMOD_OWNER_FILE
                else:
                    perms = CHMOD_ALL_FILE

            # set ownership and perms
            os.chown(dest_path, uid, uid)
            os.chmod(dest_path, perms)

        # do this after output, to get a better doc title
        print('|'+' '*level + id_, title, user, uid, private)

    print('Export complete!', file=sys.stderr, end='\n')


if __name__ == '__main__':
    main()
